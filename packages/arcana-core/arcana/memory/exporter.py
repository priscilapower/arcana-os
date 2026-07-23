"""MemoryExporter — render a store's entries as a git-diffable Markdown document.

The transparency layer's output half: agent memory lives in binary SQLite no
human can read, and this turns a set of :class:`MemoryEntry` into reviewable
Markdown — a ``# <Owner> — <Scope> Memory Export`` header, then one
``## <Type> (N entries)`` section per memory type, each entry a ``### <content>``
heading with an ``Importance / Confidence / Added`` line.

Pure and deterministic: it renders the entries it is handed, in a fixed type
order, with no clock and no I/O — the caller gathers the entries (from a browse)
and decides where the text goes (stdout or a path-guarded file). Read-only by
design: exporting never mutates the store, which stays the source of truth.
"""

from arcana.types import MemoryEntry, MemoryScope, MemoryType

#: Canonical section order — the memory-type ordering used everywhere else, so an
#: export reads the same way run to run regardless of insertion order.
_TYPE_ORDER: tuple[MemoryType, ...] = (
    MemoryType.EPISODIC,
    MemoryType.SEMANTIC,
    MemoryType.PROCEDURAL,
    MemoryType.PREFERENCE,
)


class MemoryExporter:
    """Render :class:`MemoryEntry` lists as the Markdown export document."""

    def render(self, entries: list[MemoryEntry], *, owner: str, scope: MemoryScope) -> str:
        """Return the markdown export for ``entries`` under one owner and scope.

        Entries are grouped into the canonical type sections; within a section
        they are ordered by stored importance (then recency) so the most important
        memories read first. Empty type sections are omitted. Content is rendered
        on a single heading line (newlines collapsed) so the document stays valid
        markdown even for multi-line memories.
        """
        title = f"{owner} — {scope.value.capitalize()} Memory Export"
        lines: list[str] = [f"# {title}", "", f"_{len(entries)} entries_", ""]

        by_type: dict[MemoryType, list[MemoryEntry]] = {t: [] for t in _TYPE_ORDER}
        for entry in entries:
            by_type.setdefault(entry.type, []).append(entry)

        for memory_type in _TYPE_ORDER:
            bucket = by_type.get(memory_type) or []
            if not bucket:
                continue
            bucket.sort(key=lambda e: (e.importance, e.last_accessed_at), reverse=True)
            lines.append(f"## {memory_type.value.capitalize()} ({len(bucket)} entries)")
            lines.append("")
            for entry in bucket:
                lines.append(f"### {_one_line(entry.content)}")
                lines.append(
                    f"Importance: {entry.importance:.2f} · "
                    f"Confidence: {entry.confidence:.2f} · "
                    f"Added: {entry.created_at.date().isoformat()}"
                )
                lines.append("")

        return "\n".join(lines).rstrip() + "\n"


def _one_line(content: str) -> str:
    """Collapse whitespace/newlines so multi-line content is a valid heading."""
    return " ".join(content.split())
