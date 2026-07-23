"""Tests for MemoryExporter — the Memory-Engineering markdown format. No LLM."""

from datetime import UTC, datetime
from uuid import uuid4

from arcana.memory import MemoryExporter
from arcana.types import MemoryScope, MemoryType
from tests.support.factories import make_entry


def _entry(**overrides: object):
    return make_entry(**{"created_at": datetime(2026, 7, 1, tzinfo=UTC), **overrides})


def test_header_names_owner_and_scope():
    md = MemoryExporter().render([], owner="hermit", scope=MemoryScope.PRIVATE)
    assert md.startswith("# hermit — Private Memory Export")
    assert "_0 entries_" in md


def test_sections_grouped_by_type_in_canonical_order():
    entries = [
        _entry(type=MemoryType.PREFERENCE, content="likes tea"),
        _entry(type=MemoryType.EPISODIC, content="met at noon"),
        _entry(type=MemoryType.SEMANTIC, content="sky is blue"),
    ]
    md = MemoryExporter().render(entries, owner="a", scope=MemoryScope.PRIVATE)
    # Episodic before Semantic before Preference (Procedural absent → omitted).
    assert md.index("## Episodic") < md.index("## Semantic") < md.index("## Preference")
    assert "## Procedural" not in md


def test_entry_line_carries_importance_confidence_date():
    entry = _entry(type=MemoryType.SEMANTIC, content="sky is blue", importance=0.8, confidence=0.9)
    md = MemoryExporter().render([entry], owner="a", scope=MemoryScope.PRIVATE)
    assert "### sky is blue" in md
    assert "Importance: 0.80 · Confidence: 0.90 · Added: 2026-07-01" in md


def test_section_counts_reflect_bucket_size():
    entries = [
        _entry(type=MemoryType.SEMANTIC, content="one"),
        _entry(type=MemoryType.SEMANTIC, content="two"),
    ]
    md = MemoryExporter().render(entries, owner="a", scope=MemoryScope.SHARED)
    assert "## Semantic (2 entries)" in md


def test_multiline_content_collapsed_to_single_heading_line():
    entry = _entry(type=MemoryType.SEMANTIC, content="line one\nline two\n\nline three")
    md = MemoryExporter().render([entry], owner="a", scope=MemoryScope.PRIVATE)
    assert "### line one line two line three" in md


def test_within_section_ordered_by_importance():
    entries = [
        _entry(type=MemoryType.SEMANTIC, content="low", importance=0.2),
        _entry(type=MemoryType.SEMANTIC, content="high", importance=0.9),
    ]
    md = MemoryExporter().render(entries, owner="a", scope=MemoryScope.PRIVATE)
    assert md.index("### high") < md.index("### low")


def test_scope_label_capitalized():
    md = MemoryExporter().render([_entry(agent_id=uuid4())], owner="x", scope=MemoryScope.GLOBAL)
    assert "Global Memory Export" in md
