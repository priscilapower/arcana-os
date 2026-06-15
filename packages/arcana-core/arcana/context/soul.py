from pathlib import Path

DEFAULT_SOUL_PATH = Path.home() / ".arcana" / "soul.md"


def read_soul(path: Path | None = None) -> str | None:
    """Return soul.md content, or None if missing/empty/unreadable.

    soul.md is optional, so all read errors are swallowed silently.
    """
    p = path or DEFAULT_SOUL_PATH
    try:
        text = p.read_text(encoding="utf-8").strip()
        return text or None
    except (FileNotFoundError, PermissionError, UnicodeDecodeError):
        return None
