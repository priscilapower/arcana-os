"""Tests for WorldStore — reading rules/config/spread from ~/.arcana, fail-safe."""

import json
from pathlib import Path
from uuid import uuid4

from arcana.types import RoutingRule, RuleMatchMode, Spread, SpreadLayout
from arcana.world.store import WorldStore


def _write_world(home: Path, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "world.json").write_text(json.dumps(payload))


def test_missing_world_json_returns_defaults(tmp_path):
    loaded = WorldStore(tmp_path).load()
    assert loaded.rules == []
    assert loaded.spread is None
    assert loaded.config.default_agent_id is None
    assert loaded.config.retry_window_s == 60


def test_corrupt_world_json_returns_defaults(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "world.json").write_text("{not valid json")
    loaded = WorldStore(tmp_path).load()
    assert loaded.rules == []
    assert loaded.spread is None


def test_reads_routing_rules(tmp_path):
    target = uuid4()
    rule = RoutingRule(trigger="python", target_agent_id=target, match_mode=RuleMatchMode.KEYWORD)
    _write_world(tmp_path, {"routing_rules": [json.loads(rule.model_dump_json())]})
    loaded = WorldStore(tmp_path).load()
    assert len(loaded.rules) == 1
    assert loaded.rules[0].trigger == "python"
    assert loaded.rules[0].target_agent_id == target


def test_malformed_rule_is_skipped_but_good_ones_kept(tmp_path):
    good = RoutingRule(trigger="ok", target_agent_id=uuid4())
    _write_world(
        tmp_path,
        {"routing_rules": [{"trigger": "bad — no target"}, json.loads(good.model_dump_json())]},
    )
    loaded = WorldStore(tmp_path).load()
    assert [r.trigger for r in loaded.rules] == ["ok"]


def test_reads_routing_config(tmp_path):
    default_id = uuid4()
    _write_world(tmp_path, {"default_agent_id": str(default_id), "retry_window_s": 120})
    loaded = WorldStore(tmp_path).load()
    assert loaded.config.default_agent_id == default_id
    assert loaded.config.retry_window_s == 120


def test_resolves_active_spread_by_id(tmp_path):
    agent_id = uuid4()
    spread = Spread(name="team", layout=SpreadLayout(positions={"lead": agent_id}))
    spreads_dir = tmp_path / "spreads"
    spreads_dir.mkdir(parents=True)
    (spreads_dir / f"{spread.id}.json").write_text(spread.model_dump_json())
    _write_world(tmp_path, {"active_spread": str(spread.id)})
    loaded = WorldStore(tmp_path).load()
    assert loaded.spread is not None
    assert loaded.spread.id == spread.id
    assert loaded.spread.layout.positions["lead"] == agent_id


def test_active_spread_none_means_no_spread(tmp_path):
    _write_world(tmp_path, {"active_spread": None, "routing_rules": []})
    assert WorldStore(tmp_path).load().spread is None


def test_active_spread_missing_file_degrades_to_none(tmp_path):
    _write_world(tmp_path, {"active_spread": str(uuid4())})
    assert WorldStore(tmp_path).load().spread is None


def test_active_spread_bad_id_degrades_to_none(tmp_path):
    _write_world(tmp_path, {"active_spread": "not-a-uuid"})
    assert WorldStore(tmp_path).load().spread is None


def test_inline_active_spread_is_accepted(tmp_path):
    spread = Spread(name="inline", layout=SpreadLayout(positions={"x": uuid4()}))
    _write_world(tmp_path, {"active_spread": json.loads(spread.model_dump_json())})
    loaded = WorldStore(tmp_path).load()
    assert loaded.spread is not None
    assert loaded.spread.name == "inline"
