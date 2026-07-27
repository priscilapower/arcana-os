"""Meta-safety — the fakes and the security asserts are themselves tested.

A silent no-op fake makes every green test meaningless: if ``ScriptedModel`` never
advanced, or ``FakeMCPSession`` never actually raised, the suite above would pass
while proving nothing. These tests pin the fakes' behaviour so that can't happen.

The **canary** is the sharpest of them: it weakens a guard and shows the block
disappears — proving the corresponding security test asserts against the *real*
guard and would fail if the guard regressed, rather than passing vacuously.
"""

import asyncio
import ipaddress

import pytest

from arcana.models.adapters.base import CompletionRequest
from arcana.tools.builtins.web import egress as egress_module
from arcana.tools.builtins.web.config import WebToolsConfig
from arcana.tools.builtins.web.egress import EgressBlocked, check_url
from tests.support.model import ScriptedModel, calls, text, tool_call
from tests.support.tools import FakeMCPSession, RecordingAdapter, text_result

# ---------------------------------------------------------------------------
# ScriptedModel — advances, records, and refuses to over-run
# ---------------------------------------------------------------------------


async def test_scripted_model_advances_and_records_what_it_was_offered():
    model = ScriptedModel([calls(tool_call("echo", message="hi")), text("done")])
    req_tools = CompletionRequest(
        system="s", messages=[], tools=[{"name": "echo", "description": "", "input_schema": {}}]
    )

    first = await model.complete("m", req_tools)
    second = await model.complete("m", CompletionRequest(system="s", messages=[]))

    assert first.tool_calls is not None and first.tool_calls[0]["function"]["name"] == "echo"
    assert second.content == "done" and not second.tool_calls
    # `seen` records each request in order, so a test can assert what the model saw.
    assert model.completions == 2
    assert model.seen[0].tools is not None
    assert model.seen[1].tools is None


async def test_scripted_model_raises_when_the_script_is_exhausted():
    model = ScriptedModel([text("only one")])
    await model.complete("m", CompletionRequest(system="s", messages=[]))
    with pytest.raises(AssertionError, match="ran out of turns"):
        await model.complete("m", CompletionRequest(system="s", messages=[]))


# ---------------------------------------------------------------------------
# FakeMCPSession — the fault modes actually fire
# ---------------------------------------------------------------------------


async def test_fake_mcp_session_returns_canned_results_and_records_calls():
    session = FakeMCPSession(results={"search": text_result("found")})
    result = await session.call_tool("search", {"q": "x"})
    assert result.isError is False
    assert session.calls == [("search", {"q": "x"})]


async def test_fake_mcp_session_iserror_is_carried():
    session = FakeMCPSession(results={"boom": text_result("nope", is_error=True)})
    result = await session.call_tool("boom", {})
    assert result.isError is True


async def test_fake_mcp_session_raise_on_call_actually_raises():
    session = FakeMCPSession(raise_on_call=RuntimeError("transport crash"))
    with pytest.raises(RuntimeError, match="transport crash"):
        await session.call_tool("anything", {})


async def test_fake_mcp_session_tracks_concurrent_calls():
    session = FakeMCPSession(call_delay=0.02)
    await asyncio.gather(session.call_tool("a", {}), session.call_tool("b", {}))
    # The fake itself does not serialise — overlap is observable, which is what
    # lets the concurrency test prove the *adapter* is the thing that serialises.
    assert session.max_concurrent_calls == 2


async def test_recording_adapter_records_every_execute():
    adapter = RecordingAdapter("probe")
    await adapter.execute("probe", {})
    assert adapter.executed == ["probe"]


# ---------------------------------------------------------------------------
# The canary — a weakened guard makes the block disappear
# ---------------------------------------------------------------------------


async def test_canary_weakening_the_egress_guard_unblocks_metadata(monkeypatch: pytest.MonkeyPatch):
    """If the SSRF guard is neutered, the metadata URL is no longer blocked.

    ``security/test_egress`` asserts the *opposite* (that it raises). This canary
    proves that assertion is load-bearing: weaken ``_ip_is_blocked`` to a no-op and
    the block vanishes — so a real regression in the guard would fail the security
    test, not slip past it.
    """
    url = "http://169.254.169.254/latest/meta-data/"

    # Real guard: blocked.
    with pytest.raises(EgressBlocked):
        await check_url(url, WebToolsConfig())

    # Weakened guard: the block is gone — the security test would now fail.
    monkeypatch.setattr(egress_module, "_ip_is_blocked", lambda ip: False)
    await check_url(url, WebToolsConfig())  # no longer raises


async def test_canary_matches_the_real_resolver():
    """Sanity: the literal metadata IP is what the guard actually classifies as blocked."""
    assert egress_module._ip_is_blocked(ipaddress.ip_address("169.254.169.254")) is True
    assert egress_module._ip_is_blocked(ipaddress.ip_address("93.184.216.34")) is False
