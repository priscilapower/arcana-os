"""
MarkdownFolderConnector — any folder of .md files.
Obsidian-compatible but no vault required.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from arcana.knowledge.connector import (
    ConnectorHealth,
    KnowledgeChunk,
    KnowledgeConnector,
    SyncStrategy,
)


class MarkdownFolderConnector(KnowledgeConnector):

    connector_id = "markdown_folder"
    readonly = True
    sync_strategy = SyncStrategy.PERIODIC

    def __init__(self, folder_path: str | Path) -> None:
        self.folder_path = Path(folder_path).expanduser()

    async def connect(self) -> None:
        if not self.folder_path.exists():
            raise FileNotFoundError(f"Folder not found: {self.folder_path}")

    async def search(
        self,
        query: str,
        limit: int = 5,
        tags: list[str] | None = None,
    ) -> list[KnowledgeChunk]:
        results = []
        for md_file in self.folder_path.rglob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if query.lower() in content.lower():
                results.append(KnowledgeChunk(
                    id=str(md_file.relative_to(self.folder_path)),
                    content=content,
                    source_uri=f"file://{md_file}",
                    title=md_file.stem,
                    last_modified=datetime.fromtimestamp(md_file.stat().st_mtime),
                    connector_id=self.connector_id,
                ))
            if len(results) >= limit:
                break
        return results

    async def fetch(self, chunk_id: str) -> KnowledgeChunk | None:
        path = self.folder_path / chunk_id
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        return KnowledgeChunk(
            id=chunk_id,
            content=content,
            source_uri=f"file://{path}",
            title=path.stem,
            connector_id=self.connector_id,
        )

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_id=self.connector_id,
            healthy=self.folder_path.exists(),
        )
