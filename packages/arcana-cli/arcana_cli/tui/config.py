"""Settings for the interactive app: the ``ui`` block of ``config.json`` and env tunables.

* :class:`UiConfig` — user preferences read from ``<ARCANA_HOME>/config.json``'s
  ``ui`` block (``{"ui": {"mouse": false}}``). A missing file, block or key falls
  back to the defaults, and so does an unreadable file: a broken preference must
  never stop the session from starting.
* :class:`TuiTunables` — operator knobs, overridable via ``ARCANA_TUI_*`` env
  vars, read once at import.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class UiConfig(BaseModel):
    """The ``ui`` block of ``config.json``.

    ``mouse`` turns terminal mouse capture on: wheel scrolling of the transcript
    and clickable dialogs, at the cost of native drag-select needing a modifier
    key (Shift, or Option in iTerm2). Off restores plain selection.
    """

    model_config = ConfigDict(extra="ignore")

    mouse: bool = True


class _ConfigFile(BaseModel):
    """The part of ``config.json`` this module reads; every other key is ignored."""

    model_config = ConfigDict(extra="ignore")

    ui: UiConfig = Field(default_factory=UiConfig)


def load_ui_config(home: Path) -> UiConfig:
    """Read the ``ui`` block from ``<home>/config.json``; defaults on anything missing or malformed."""
    try:
        return _ConfigFile.model_validate_json((home / "config.json").read_bytes()).ui
    except (OSError, ValidationError):
        return UiConfig()


class TuiTunables(BaseSettings):
    """Interactive-app knobs, overridable via ``ARCANA_TUI_*`` env vars."""

    model_config = SettingsConfigDict(env_prefix="ARCANA_TUI_", extra="ignore")

    # How many transcript blocks are printed to the terminal when the app exits;
    # older ones are summarised in a one-line note.
    replay_blocks: int = Field(default=500, ge=1)
    # How many times a second a streaming block redraws while text arrives. The
    # stream itself never waits on a redraw, so a fast model can't queue work.
    stream_fps: int = Field(default=30, ge=1, le=120)


TUNABLES = TuiTunables()

REPLAY_BLOCKS = TUNABLES.replay_blocks

STREAM_FPS = TUNABLES.stream_fps
