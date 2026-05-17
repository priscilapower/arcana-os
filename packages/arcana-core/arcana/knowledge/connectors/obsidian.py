"""
ObsidianConnector — read (and optionally write) a local Obsidian vault.

Two connection modes:
  1. Direct vault path (default) — reads .md files directly from disk
  2. Local REST API — uses obsidian-local-rest-api plugin for live queries

Sync strategy: PERIODIC (indexes embeddings in background)
Source of truth: always the vault. Arcana never owns Obsidian notes.

Write-back: opt-in. When enabled, Arcana writes to a dedicated
/Arcana/ subfolder inside the vault — never touches user's own notes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from arcana.knowledge.connector import (
    ConnectorHealth,
    KnowledgeChunk,
    KnowledgeConnector,
    SyncStrategy,
)


class ObsidianConnector(KnowledgeConnector):

    connector_id = "obsidian"
    readonly = False          # can write to /Arcana/ subfolder
    requires_auth = False     # vault path is sufficient
    sync_strategy = SyncStrategy.PERIODIC

    # Arcana writes agent memories here — never touches user's notes
    ARCANA_SUBFOLDER = "Arcana"

    def __init__(
        self,
        vault_path: str | Path,
        write_back_enabled: bool = False,
        rest_api_url: str | None = None,   # e.g. http://localhost:27123
    ) -> None:
        self.vault_path = Path(vault_path).expanduser()
        self.write_back_enabled = write_back_enabled
        self.rest_api_url = rest_api_url
        self._use_rest = rest_api_url is not None

    async def connect(self) -> None:
        if not self.vault_path.exists():
            raise FileNotFoundError(
                f"Obsidian vault not found: {self.vault_path}"
            )
        if self._use_rest:
            # Verify REST API is reachable
            import httpx
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{self.rest_api_url}/")
                r.raise_for_status()

    async def search(
        self,
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[KnowledgeChunk]:
        """
        Search vault notes.
        Phase 1: simple keyword scan of markdown files.
        Phase 2: embedding-based semantic search after sync().
        """
        results: list[KnowledgeChunk] = []
        query_lower = query.lower()

        for md_file in self.vault_path.rglob("*.md"):
            # Skip Arcana's own subfolder to avoid circular reads
            if self.ARCANA_SUBFOLDER in md_file.parts:
                continue

            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            if query_lower not in content.lower():
                continue

            chunk = self._file_to_chunk(md_file, content)

            if tags:
                if not any(t in chunk.tags for t in tags):
                    continue

            results.append(chunk)
            if len(results) >= limit:
                break

        return results

    async def fetch(self, chunk_id: str) -> KnowledgeChunk | None:
        """Fetch live content of a note by its vault-relative path."""
        file_path = self.vault_path / chunk_id
        if not file_path.exists():
            return None
        content = file_path.read_text(encoding="utf-8")
        return self._file_to_chunk(file_path, content)

    async def write_back(self, chunk: KnowledgeChunk) -> str | None:
        """
        Write a chunk to the vault's /Arcana/ subfolder.
        Only runs if write_back_enabled=True.
        Never writes to user's own notes.
        """
        if not self.write_back_enabled:
            return None

        arcana_dir = self.vault_path / self.ARCANA_SUBFOLDER
        arcana_dir.mkdir(exist_ok=True)

        filename = f"{chunk.id}.md"
        file_path = arcana_dir / filename
        frontmatter = self._to_frontmatter(chunk)
        file_path.write_text(f"{frontmatter}\n\n{chunk.content}")
        return str(file_path.relative_to(self.vault_path))

    async def health_check(self) -> ConnectorHealth:
        healthy = self.vault_path.exists()
        return ConnectorHealth(
            connector_id=self.connector_id,
            healthy=healthy,
            message="" if healthy else f"Vault not found: {self.vault_path}",
        )

    # ------------------------------------------------------------------

    def _file_to_chunk(self, path: Path, content: str) -> KnowledgeChunk:
        rel_path = str(path.relative_to(self.vault_path))
        tags = self._extract_tags(content)
        stat = path.stat()
        return KnowledgeChunk(
            id=rel_path,
            content=content,
            source_uri=f"obsidian://{rel_path}",
            title=path.stem,
            tags=tags,
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            connector_id=self.connector_id,
        )

    def _extract_tags(self, content: str) -> list[str]:
        """Extract #tags and frontmatter tags from markdown."""
        tags: list[str] = []
        for line in content.splitlines():
            # Inline tags
            for word in line.split():
                if word.startswith("#") and len(word) > 1:
                    tags.append(word[1:].strip(".,;:"))
            # Frontmatter tags: "tags: [a, b]" or "tags:\n  - a"
            if line.strip().startswith("tags:"):
                raw = line.split(":", 1)[-1].strip()
                if raw.startswith("["):
                    try:
                        tags.extend(json.loads(raw))
                    except json.JSONDecodeError:
                        pass
        return list(set(tags))

    def _to_frontmatter(self, chunk: KnowledgeChunk) -> str:
        lines = ["---"]
        if chunk.title:
            lines.append(f"title: {chunk.title}")
        if chunk.tags:
            lines.append(f"tags: [{', '.join(chunk.tags)}]")
        lines.append(f"source: {chunk.source_uri}")
        lines.append("created_by: arcana")
        lines.append("---")
        return "\n".join(lines)
