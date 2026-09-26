"""arcana memory — inspect, audit, forget, connect, and export agent memory.

Agent memory lives in binary SQLite under ``~/.arcana`` that no human can see;
this group is the terminal surface that makes it inspectable, exportable, and
*forgettable*. Every command is thin over :class:`MemoryFederation`: build the
tier stack for a target, run one coroutine, render (Rich table or ``--json``).

The destructive/filesystem-touching commands are the centre of gravity.
``forget`` refuses to delete a GLOBAL entry (the World owns GLOBAL) and confirms
before a hard delete; ``connect`` / ``export --out`` validate every path against
the filesystem guardrails (:mod:`arcana.memory.paths`) before any I/O. Reads are
offline-first: ``list`` / ``inspect`` / ``adapters`` / ``export`` and
``search --mode keyword`` work with no embedding provider.

Every command body is a renderer-agnostic coroutine that takes a
:class:`~arcana_cli.ui.renderer.Renderer`: its result is a
:class:`~arcana_cli.ui.renderer.Presentable` (a Rich view and the ``--json``
document of the same data), and a failure — a core memory error included (see
:func:`_memory_errors`) — goes through :func:`~arcana_cli.ui.renderer.fail`.
Each body runs its build → use → close in the single ``run_async`` call of its
Typer callback: the private SQLite handle is bound to the event loop that opened
it, so splitting the work across loops would break it — and the federation is
always closed in a ``finally`` so no connection leaks.
"""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, NoReturn
from uuid import UUID, uuid4

import typer
from rich.markup import escape

from arcana.agents.registry import AgentRegistry
from arcana.cards.engine import CardEngine
from arcana.cards.registry import get_registry
from arcana.memory import (
    EXPORT_MAX_ENTRIES,
    EmbeddingGateway,
    KnowledgeConnectorStore,
    MemoryExporter,
    MemoryFederation,
    atomic_write_text,
    build_federation,
    connector_adapter,
    resolve_existing_dir,
    resolve_out_path,
)
from arcana.memory.decay import effective_importance, resolve_decay_profiles
from arcana.memory.errors import GlobalDeleteRefused, MemoryError, ReadOnlyTierDelete
from arcana.types import (
    Card,
    DecayProfile,
    KnowledgeConnector,
    KnowledgeConnectorKind,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetrievalMode,
)
from arcana.types._utils import now_utc
from arcana.types.agent import Agent as AgentRecord
from arcana_cli._async import run_async
from arcana_cli._render import EXIT_DENIED, EXIT_NOT_FOUND, truncate
from arcana_cli.commands.run import resolve_embedding_gateway
from arcana_cli.commands.tools import resolve_agent
from arcana_cli.constants import AGENTS_BASE, ARCANA_HOME, MEMORY_ADAPTERS_PATH
from arcana_cli.ui.renderer import Renderer, Verbatim, View, confirm_or_cancel, fail, lines, renderer_for
from arcana_cli.ui.theme import GREEN, ORANGE, RED, TXT2, TXT3, dim, hl, make_table, ok, warn

app = typer.Typer(
    help="Inspect, audit, forget, connect, and export agent memory "
    "(list / search / inspect / forget / connect / adapters / export)."
)

_SCOPE_COLORS: dict[MemoryScope, str] = {
    MemoryScope.PRIVATE: TXT2,
    MemoryScope.SHARED: ORANGE,
    MemoryScope.GLOBAL: GREEN,
}


# ---------------------------------------------------------------------------
# Federation assembly + scope resolution
# ---------------------------------------------------------------------------


def _load_embedding_gateway() -> EmbeddingGateway | None:
    """The GLOBAL-tier embedder, or ``None`` for a private-only stack.

    Indirection over the shared resolver so tests can substitute a gateway (e.g.
    an empty one that mounts a keyword-only global tier without the embed extra).
    """
    return resolve_embedding_gateway()


def _decay_profiles_for(record: AgentRecord) -> dict[MemoryType, DecayProfile]:
    """The agent's card-driven per-type decay half-lives (The World never forgets)."""
    config = CardEngine(get_registry()).resolve(record.card, record.modifier_cards)
    return resolve_decay_profiles(config.decay_config, world=record.card == Card.WORLD)


async def _open_federation(record: AgentRecord, *, embedding: EmbeddingGateway | None) -> MemoryFederation:
    """Assemble the tier stack for one agent: private + (global) tiers.

    Knowledge connectors are deliberately **not** mounted here — they are external
    read-only sources addressed by name via ``--connector`` (see
    :func:`_resolve_connector`), kept out of the ``SHARED`` pool namespace so a
    reference folder never masquerades as writable inter-agent memory.
    """
    return await build_federation(
        record.id,
        home=ARCANA_HOME,
        embedding=embedding,
        decay_profiles=_decay_profiles_for(record),
    )


def _resolve_connector(r: Renderer, name: str) -> KnowledgeConnector:
    """Resolve a registered connector by name, or exit (``1`` corrupt / ``2`` unknown)."""
    store = KnowledgeConnectorStore(MEMORY_ADAPTERS_PATH)
    try:
        store.load()
    except MemoryError as exc:
        fail(r, str(exc))
    connector = store.get(name)
    if connector is None:
        fail(
            r,
            f"No connector named {name!r}.",
            dim("  See registered connectors: arcana memory adapters"),
            code=EXIT_NOT_FOUND,
        )
    return connector


async def _connector_entries(connector: KnowledgeConnector, query: MemoryQuery) -> list[MemoryEntry]:
    """Read a connector's notes directly (offline, keyword). Scope-agnostic."""
    return await connector_adapter(connector, uuid4()).search(query)


def _resolve_scope(scope: MemoryScope | None, pool: str | None) -> MemoryScope:
    """Map ``--scope`` / ``--pool`` to a target scope. ``--pool`` implies SHARED."""
    if pool is not None:
        return MemoryScope.SHARED
    return scope or MemoryScope.PRIVATE


@contextmanager
def _memory_errors(r: Renderer) -> Generator[None]:
    """Map the expected core memory failures to a clean :func:`fail` with the right exit code.

    Keeps every command body free of error plumbing: an expected memory failure
    is an error message and an exit code (no traceback), while a ``typer.Exit``
    raised inside (e.g. by ``resolve_agent``) passes straight through.
    """
    try:
        yield
    except GlobalDeleteRefused as exc:
        fail(
            r,
            f"Entry {exc.memory_id} lives in the GLOBAL tier — the World owns GLOBAL.",
            dim("  GLOBAL entries are pruned by The World, not deleted from the CLI."),
            code=EXIT_DENIED,
        )
    except ReadOnlyTierDelete as exc:
        fail(
            r,
            f"Entry {exc.memory_id} is owned by a read-only source ({exc.tier}).",
            dim("  Edit the connected folder to remove it; a connector is a reference, not a store."),
            code=EXIT_DENIED,
        )
    except MemoryError as exc:  # PathSafetyError included
        fail(r, str(exc))


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _scope_label(scope: MemoryScope, pool: str | None = None) -> str:
    body = scope.value if pool is None else f"{scope.value}:{pool}"
    return f"[{_SCOPE_COLORS.get(scope, TXT3)}]{body}[/]"


def _short_id(memory_id: UUID) -> str:
    return str(memory_id)[:8]


def _entry_row(entry: MemoryEntry) -> dict[str, Any]:
    """The stable ``--json`` shape for one entry (full ids, no secrets)."""
    return {
        "id": str(entry.id),
        "type": entry.type.value,
        "importance": entry.importance,
        "confidence": entry.confidence,
        "scope": entry.scope.value,
        "pool": entry.pool_name,
        "pinned": entry.pinned,
        "content": entry.content,
        "created": entry.created_at.isoformat(),
    }


def _validate_read_target(
    r: Renderer, agent: str | None, connector: str | None, pool: str | None, scope: MemoryScope | None
) -> None:
    """Require exactly one read source. ``--connector`` is its own thing, never a pool.

    An agent's memory (optionally refined by ``--pool`` / ``--scope``) and an
    external ``--connector`` are distinct sources, so mixing them is a usage error
    — this is what keeps read-only connectors out of the shared-pool namespace.
    """
    if connector is not None:
        if agent is not None or pool is not None or scope is not None:
            fail(r, "--connector reads an external source; don't combine it with --agent/--pool/--scope.")
        return
    if agent is None:
        fail(r, "Specify --agent <name> (or --connector <name> for an external knowledge source).")


def _entries_view(entries: list[MemoryEntry], *, title: str, scope_col: bool) -> View:
    """An entry list: a Rich table, or an array of entry rows; shared by the list/search paths."""
    rows = [_entry_row(e) for e in entries]
    if not entries:
        return View(dim(f"No entries — {title}."), rows)
    table = make_table(title)
    table.add_column("ID", style=TXT3)
    table.add_column("Type")
    table.add_column("Imp.", justify="right")
    table.add_column("Conf.", justify="right")
    if scope_col:
        table.add_column("Scope")
    table.add_column("Content", style=TXT2)
    for e in entries:
        row = [_short_id(e.id), e.type.value, f"{e.importance:.2f}", f"{e.confidence:.2f}"]
        if scope_col:
            row.append(_scope_label(e.scope, e.pool_name))
        row.append(truncate(e.content))
        table.add_row(*row)
    return View(table, rows)


# ---------------------------------------------------------------------------
# arcana memory list
# ---------------------------------------------------------------------------


@app.command("list")
def list_cmd(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    connector: str | None = typer.Option(None, "--connector", help="Read a registered knowledge connector by name"),
    pool: str | None = typer.Option(None, "--pool", help="Read a shared memory pool (agent memory)"),
    scope: MemoryScope | None = typer.Option(None, "--scope", help="private | shared | global"),  # noqa: B008
    type_: MemoryType | None = typer.Option(None, "--type", help="Filter by memory type"),  # noqa: B008
    limit: int = typer.Option(20, "--limit", help="Maximum entries to show"),
    min_importance: float = typer.Option(0.0, "--min-importance", help="Only entries at/above this importance"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List entries by importance — from an agent's memory or a connector (offline)."""
    run_async(
        list_memory(
            renderer_for(json_),
            agent=agent,
            connector=connector,
            pool=pool,
            scope=scope,
            type_=type_,
            limit=limit,
            min_importance=min_importance,
        )
    )


async def list_memory(
    r: Renderer,
    *,
    agent: str | None,
    connector: str | None,
    pool: str | None,
    scope: MemoryScope | None,
    type_: MemoryType | None,
    limit: int,
    min_importance: float,
) -> None:
    """Entries by importance, from an agent's memory (``agent``) or a connector."""
    _validate_read_target(r, agent, connector, pool, scope)

    if connector is not None:
        conn = _resolve_connector(r, connector)
        query = MemoryQuery(
            type=type_, limit=limit, min_importance=min_importance, retrieval_mode=RetrievalMode.keyword
        )
        with _memory_errors(r):
            entries = await _connector_entries(conn, query)
        r.emit(_entries_view(entries, title=f"{conn.name} · connector", scope_col=False))
        return

    assert agent is not None  # noqa: S101 — _validate_read_target guarantees agent set when connector is None
    record = resolve_agent(r, agent)
    target_scope = _resolve_scope(scope, pool)

    with _memory_errors(r):
        fed = await _open_federation(record, embedding=_load_embedding_gateway())
        try:
            entries = await fed.browse(
                MemoryQuery(
                    scope=target_scope,
                    pool_name=pool,
                    type=type_,
                    limit=limit,
                    min_importance=min_importance,
                    retrieval_mode=RetrievalMode.keyword,
                )
            )
        finally:
            await fed.aclose()
    r.emit(_entries_view(entries, title=f"{record.name} · {target_scope.value} memory", scope_col=True))


# ---------------------------------------------------------------------------
# arcana memory search
# ---------------------------------------------------------------------------


@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Search text"),
    agent: str | None = typer.Option(None, "--agent", "-a", help="Agent name or UUID"),
    connector: str | None = typer.Option(None, "--connector", help="Search a registered knowledge connector by name"),
    pool: str | None = typer.Option(None, "--pool", help="Search a shared memory pool (agent memory)"),
    scope: MemoryScope | None = typer.Option(None, "--scope", help="private | shared | global"),  # noqa: B008
    mode: RetrievalMode = typer.Option(RetrievalMode.semantic, "--mode", help="semantic | hybrid | keyword"),  # noqa: B008
    limit: int = typer.Option(10, "--limit", help="Maximum results"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Search an agent's memory or a connector. semantic/hybrid degrade to keyword with no embedder."""
    run_async(
        search_memory(
            renderer_for(json_),
            query,
            agent=agent,
            connector=connector,
            pool=pool,
            scope=scope,
            mode=mode,
            limit=limit,
        )
    )


async def search_memory(
    r: Renderer,
    query: str,
    *,
    agent: str | None,
    connector: str | None,
    pool: str | None,
    scope: MemoryScope | None,
    mode: RetrievalMode,
    limit: int,
) -> None:
    """Search an agent's memory (``agent``) or a connector for ``query``."""
    _validate_read_target(r, agent, connector, pool, scope)

    if connector is not None:
        conn = _resolve_connector(r, connector)
        # A connector folder is keyword-only, so the mode is moot — always FTS-scan.
        with _memory_errors(r):
            entries = await _connector_entries(
                conn, MemoryQuery(text=query, retrieval_mode=RetrievalMode.keyword, limit=limit)
            )
        r.emit(_entries_view(entries, title=f"{conn.name} · search '{truncate(query, 30)}'", scope_col=False))
        return

    assert agent is not None  # noqa: S101 — _validate_read_target guarantees agent set when connector is None
    record = resolve_agent(r, agent)
    target_scope = _resolve_scope(scope, pool)
    embedding = _load_embedding_gateway()
    if mode is not RetrievalMode.keyword and embedding is None:
        r.note(dim(escape("No embedding provider — keyword search (FTS5). Install the [embed] extra for semantic.")))
        mode = RetrievalMode.keyword

    with _memory_errors(r):
        fed = await _open_federation(record, embedding=embedding)
        try:
            results = await fed.search(
                MemoryQuery(text=query, retrieval_mode=mode, scope=target_scope, pool_name=pool, limit=limit)
            )
        finally:
            await fed.aclose()
    r.emit(_entries_view(results, title=f"{record.name} · search '{truncate(query, 30)}'", scope_col=True))


# ---------------------------------------------------------------------------
# arcana memory inspect
# ---------------------------------------------------------------------------


@app.command("inspect")
def inspect_cmd(
    memory_id: str = typer.Argument(..., help="Memory entry UUID"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name or UUID"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Show one entry in full, with its decay factor and effective importance."""
    run_async(inspect_memory(renderer_for(json_), memory_id, agent=agent))


async def inspect_memory(r: Renderer, memory_id: str, *, agent: str) -> None:
    """One entry in full, with its decay factor and effective importance."""
    record = resolve_agent(r, agent)
    mid = _parse_uuid(r, memory_id)
    profiles = _decay_profiles_for(record)

    with _memory_errors(r):
        fed = await _open_federation(record, embedding=_load_embedding_gateway())
        try:
            entry = await fed.get(mid)
        finally:
            await fed.aclose()
    if entry is None:
        _no_such_memory(r, memory_id, record)

    profile = profiles[entry.type]
    effective = effective_importance(entry, profile, now_utc())
    factor = (effective / entry.importance) if entry.importance > 0 else 1.0

    human: list[str] = [
        f"\n  {hl('Content:')}      {entry.content}",
        f"  {hl('ID:')}           {entry.id}",
        f"  {hl('Type:')}         {entry.type.value}",
        f"  {hl('Scope:')}        {_scope_label(entry.scope, entry.pool_name)}",
        f"  {hl('Importance:')}   {entry.importance:.2f}  ({hl('effective')} {effective:.2f}, decay ×{factor:.2f})",
        f"  {hl('Confidence:')}   {entry.confidence:.2f}  ({entry.confidence_source.value})",
        f"  {hl('Created:')}      {entry.created_at.date().isoformat()}",
        f"  {hl('Last read:')}    {entry.last_accessed_at.date().isoformat()}  ({entry.access_count}×)",
    ]
    if entry.source_session_id:
        human.append(f"  {hl('Session:')}      {entry.source_session_id}")
    if entry.has_conflict:
        human.append(warn("  ⚠ conflict flagged — awaiting resolution"))
    if entry.archived:
        human.append(dim("  (archived)"))
    human.append("")
    detail = {
        **_entry_row(entry),
        "confidence_source": entry.confidence_source.value,
        "last_accessed": entry.last_accessed_at.isoformat(),
        "access_count": entry.access_count,
        "decay_factor": round(factor, 4),
        "effective_importance": round(effective, 4),
        "source_session_id": str(entry.source_session_id) if entry.source_session_id else None,
        "has_conflict": entry.has_conflict,
        "archived": entry.archived,
    }
    r.emit(View(lines(*human), detail))


# ---------------------------------------------------------------------------
# arcana memory forget
# ---------------------------------------------------------------------------


@app.command("forget")
def forget_cmd(
    memory_id: str = typer.Argument(..., help="Memory entry UUID"),
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name or UUID"),
    archive: bool = typer.Option(False, "--archive", help="Soft-delete (recoverable) instead of a hard delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Delete one entry. Refuses GLOBAL (the World owns it); hard-deletes by default.

    Asks before deleting unless ``--yes``; under ``--json`` there is no one to
    ask, so the question fails closed naming ``--yes``.
    """
    run_async(forget_memory(renderer_for(json_), memory_id, agent=agent, archive=archive, yes=yes))


def _no_such_memory(r: Renderer, memory_id: str, record: AgentRecord) -> NoReturn:
    fail(r, f"No memory with id {memory_id!r} for agent '{record.name}'.", code=EXIT_NOT_FOUND)


async def forget_memory(r: Renderer, memory_id: str, *, agent: str, archive: bool, yes: bool) -> None:
    """Forget one entry once confirmed (or with ``yes``); GLOBAL is refused before anything is asked."""
    record = resolve_agent(r, agent)
    mid = _parse_uuid(r, memory_id)
    hard = not archive

    with _memory_errors(r):
        fed = await _open_federation(record, embedding=_load_embedding_gateway())
        try:
            entry = await fed.get(mid)
            if entry is None:
                _no_such_memory(r, memory_id, record)
            # Refuse GLOBAL before prompting — the World owns it.
            if entry.scope is MemoryScope.GLOBAL:
                raise GlobalDeleteRefused(mid)
            if not yes:
                verb = "Archive" if archive else "Permanently delete"
                await confirm_or_cancel(r, f"{verb} memory {_short_id(mid)} ({truncate(entry.content, 40)})?")
            result = await fed.forget(mid, hard=hard)
        finally:
            await fed.aclose()

    if not result.found:  # gone between the lookup and the delete
        _no_such_memory(r, memory_id, record)

    scope = result.scope
    action = "Archived" if archive else "Deleted"
    r.emit(
        View(
            ok(f"{action} memory {_short_id(mid)} from {scope.value if scope else '?'} memory."),
            {
                "id": str(mid),
                "forgotten": True,
                "hard": hard,
                "scope": scope.value if scope else None,
                "pool": result.pool_name,
            },
        )
    )


# ---------------------------------------------------------------------------
# arcana memory connect
# ---------------------------------------------------------------------------


connect_app = typer.Typer(help="Register a folder of notes as an external knowledge connector (obsidian / markdown).")
app.add_typer(connect_app, name="connect")


@connect_app.command("obsidian")
def connect_obsidian(
    vault: str = typer.Option(..., "--vault", help="Path to the Obsidian vault folder"),
    name: str | None = typer.Option(None, "--name", help="Connector name (defaults to the folder name)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Register an Obsidian vault as an external read-only knowledge connector."""
    run_async(connect_folder(renderer_for(json_), KnowledgeConnectorKind.OBSIDIAN, vault, name))


@connect_app.command("markdown")
def connect_markdown(
    path: str = typer.Option(..., "--path", help="Path to the Markdown folder"),
    name: str | None = typer.Option(None, "--name", help="Connector name (defaults to the folder name)"),
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """Register a plain folder of Markdown notes as an external read-only knowledge connector."""
    run_async(connect_folder(renderer_for(json_), KnowledgeConnectorKind.MARKDOWN, path, name))


async def connect_folder(r: Renderer, kind: KnowledgeConnectorKind, raw_path: str, name: str | None) -> None:
    """Validate the path, build the connector reference, and persist it.

    Path validation (``PathSafetyError``) and registry I/O (``MemoryStorageError``
    on a corrupt or unwritable file) share one ``MemoryError`` guard, so both fail
    with a clean message and ``EXIT_ERROR`` rather than a traceback.
    """
    with _memory_errors(r):
        resolved = resolve_existing_dir(raw_path)
        connector = KnowledgeConnector(name=name or resolved.name, kind=kind, path=str(resolved))
        store = KnowledgeConnectorStore(MEMORY_ADAPTERS_PATH)
        store.load()
        store.add(connector)

    r.emit(
        View(
            lines(
                ok(f"Connected {kind.value} '{connector.name}'."),
                dim(f"  {connector.path}"),
                dim(f"  Read it: arcana memory list --connector {connector.name}"),
                dim("  Inspect connectors: arcana memory adapters"),
            ),
            {"name": connector.name, "kind": connector.kind.value, "path": connector.path},
        )
    )


# ---------------------------------------------------------------------------
# arcana memory adapters
# ---------------------------------------------------------------------------


@app.command("adapters")
def adapters_cmd(
    json_: bool = typer.Option(False, "--json", help="Emit JSON"),
) -> None:
    """List registered knowledge connectors and probe each one's health."""
    run_async(list_adapters(renderer_for(json_)))


async def _connector_health(connector: KnowledgeConnector) -> tuple[bool, int]:
    adapter = connector_adapter(connector, uuid4())
    health = await adapter.health_check()
    if not health.healthy:
        return (False, 0)
    try:
        notes = await adapter.scan()
    except Exception:  # noqa: BLE001 — health listing must never crash on a bad vault
        return (False, 0)
    return (True, len(notes))


async def list_adapters(r: Renderer) -> None:
    """Every registered knowledge connector with its health and note count."""
    # The registry load is guarded so a corrupt file exits cleanly
    # (MemoryStorageError → EXIT_ERROR) rather than as a raw traceback.
    with _memory_errors(r):
        store = KnowledgeConnectorStore(MEMORY_ADAPTERS_PATH)
        store.load()
        found: list[tuple[KnowledgeConnector, bool, int]] = []
        for connector in store.list():
            healthy, count = await _connector_health(connector)
            found.append((connector, healthy, count))

    rows = [
        {"name": c.name, "kind": c.kind.value, "path": c.path, "healthy": healthy, "notes": count}
        for c, healthy, count in found
    ]
    if not found:
        r.emit(View(dim("No knowledge connectors. Connect one: arcana memory connect obsidian --vault <path>"), rows))
        return
    table = make_table("Knowledge connectors")
    table.add_column("Name", style="bold")
    table.add_column("Kind")
    table.add_column("Health")
    table.add_column("Notes", justify="right")
    table.add_column("Path", style=TXT3)
    for c, healthy, count in found:
        health_cell = f"[{GREEN}]healthy[/]" if healthy else f"[{RED}]unhealthy[/]"
        table.add_row(c.name, c.kind.value, health_cell, str(count) if healthy else dim("—"), c.path)
    r.emit(View(table, rows))


# ---------------------------------------------------------------------------
# arcana memory export
# ---------------------------------------------------------------------------


@app.command("export")
def export_cmd(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Export one agent's private memory"),
    pool: str | None = typer.Option(None, "--pool", help="Export a shared pool"),
    all_: bool = typer.Option(False, "--all", help="Export every agent's private memory"),
    out: str | None = typer.Option(None, "--out", help="Write to a file (default: stdout)"),
    type_: MemoryType | None = typer.Option(None, "--type", help="Only export this memory type"),  # noqa: B008
    yes: bool = typer.Option(False, "--yes", "-y", help="Overwrite --out if it exists"),
    json_: bool = typer.Option(False, "--json", help="Emit a JSON summary instead of markdown"),
) -> None:
    """Export memory to a git-diffable Markdown document (read-only)."""
    run_async(export_memory(renderer_for(json_), agent=agent, pool=pool, all_=all_, out=out, type_=type_, yes=yes))


async def export_memory(
    r: Renderer,
    *,
    agent: str | None,
    pool: str | None,
    all_: bool,
    out: str | None,
    type_: MemoryType | None,
    yes: bool,
) -> None:
    """Render the chosen memory as Markdown: to ``out``, or as the document itself (a summary under ``--json``)."""
    chosen = [flag for flag in (agent is not None, pool is not None, all_) if flag]
    if len(chosen) != 1:
        fail(r, "Choose exactly one of --agent, --pool, or --all.")

    # Resolve the output path up front (path-guarded) so a bad target fails before work.
    out_path = None
    if out is not None:
        with _memory_errors(r):
            out_path = resolve_out_path(out)

    embedding = _load_embedding_gateway()
    exporter = MemoryExporter()

    async def _dump_store(record: AgentRecord, scope: MemoryScope, pool_name: str | None) -> str:
        fed = await _open_federation(record, embedding=embedding)
        try:
            entries = await fed.browse(
                MemoryQuery(
                    scope=scope,
                    pool_name=pool_name,
                    type=type_,
                    limit=EXPORT_MAX_ENTRIES,
                    retrieval_mode=RetrievalMode.keyword,
                )
            )
        finally:
            await fed.aclose()
        owner = pool_name or record.name
        return exporter.render(entries, owner=owner, scope=scope)

    with _memory_errors(r):
        if all_:
            agents = AgentRegistry(AGENTS_BASE).list()
            sections = [await _dump_store(a, MemoryScope.PRIVATE, None) for a in agents]
            markdown = "\n\n".join(sections) if sections else "# (no agents)\n"
        else:
            record = resolve_agent(r, agent) if agent is not None else _any_agent(r)
            scope = MemoryScope.SHARED if pool else MemoryScope.PRIVATE
            markdown = await _dump_store(record, scope, pool)

    size = len(markdown.encode("utf-8"))
    if out_path is not None:
        with _memory_errors(r):
            atomic_write_text(out_path, markdown, overwrite=yes)
        summary = {"exported": True, "out": str(out_path), "bytes": size}
        r.emit(View(ok(f"Exported memory to {out_path.name}."), summary))
        return
    # The document itself, verbatim, so `arcana memory export ... > file` and `| less` work.
    r.emit(View(Verbatim(markdown), {"exported": True, "bytes": size}))


def _any_agent(r: Renderer) -> AgentRecord:
    """The sole agent when a pool export omits ``--agent`` (a pool needs an owner id).

    A shared pool is agent-independent, but building the tier stack needs *an*
    agent identity to scope the private store; when the caller didn't name one and
    exactly one agent exists, use it, else ask for ``--agent``.
    """
    agents = AgentRegistry(AGENTS_BASE).list()
    if len(agents) == 1:
        return agents[0]
    fail(r, "Specify --agent: more than one agent exists (a pool export still needs an agent context).")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _parse_uuid(r: Renderer, raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError:
        fail(r, f"Invalid memory id {raw!r} — expected a UUID.")
