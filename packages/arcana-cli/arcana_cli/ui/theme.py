"""Arcana OS CLI design system.

Centralizes all Rich styles, color tokens, glyphs, and UI factories.
Commands should import from here instead of writing ad-hoc markup strings.

Color tokens are hex-truecolor approximations of the OKLCH values defined in
Arcana's design system. Design System v3.0 — Cyan + Amber canonical pairing, dark-first.
"""

from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table

from arcana.types.card import Card

# ---------------------------------------------------------------------------
# Glyphs  (Design System - Unicode only, no icon font)
# ---------------------------------------------------------------------------
PROMPT = "✦"  # brand glyph — prompt prefix, eyebrow, bullet
BLEND = "◈"  # modifier / blend cards
REVERSED = "⟲"  # reversed state
CHECK = "✓"  # success
CROSS = "✗"  # error
STAR = "★"  # ratings (amber)
SEP = "·"  # separator (muted)
ARROW = "→"  # flow / links

# ---------------------------------------------------------------------------
# Color tokens  (hex truecolor from OKLCH design tokens)
# ---------------------------------------------------------------------------

# Cyan — primary accent  --accent*
ACCENT = "#22d3ee"  # oklch(72% 0.14 198)
ACCENT_DIM = "#0ea5e9"  # oklch(60% 0.14 198)
ACCENT_BRIGHT = "#67e8f9"  # oklch(82% 0.12 198)

# Amber — secondary accent  --accent2* (commands, warmth, thinking)
AMBER = "#fbbf24"  # oklch(80% 0.15  78)
AMBER_DIM = "#d97706"  # oklch(68% 0.15  78)

# Semantic  --green / --red / --orange
GREEN = "#22c55e"  # oklch(70% 0.17 150)  success / running
RED = "#ef4444"  # oklch(64% 0.18  28)  error / reversed
ORANGE = "#f97316"  # oklch(70% 0.18  50)  warning

# Text hierarchy  --txt / --txt2 / --txt3
TXT = "#f5f0e6"  # --txt   primary text (warm white on dark)
TXT2 = "#94a3b8"  # --txt2  muted
TXT3 = "#64748b"  # --txt3  subtle / placeholder

# ---------------------------------------------------------------------------
# Per-card accent hues  (the 22 Major Arcana, fixed hues)
# Hex truecolor approximations of the OKLCH values in the design doc.
# ---------------------------------------------------------------------------
CARD_COLORS: dict[Card, str] = {
    Card.FOOL: "#e8a520",  # oklch(78% 0.18  75) — golden amber
    Card.MAGICIAN: "#e05535",  # oklch(65% 0.18  28) — orange-red
    Card.HIGH_PRIESTESS: "#1a8fcc",  # oklch(62% 0.17 225) — sky blue
    Card.EMPRESS: "#d85089",  # oklch(68% 0.18 340) — rose
    Card.EMPEROR: "#c93030",  # oklch(58% 0.18  12) — deep red
    Card.HIEROPHANT: "#2a9650",  # oklch(60% 0.17 148) — forest green
    Card.LOVERS: "#e84b6e",  # oklch(72% 0.17 355) — rose-red
    Card.CHARIOT: "#3b7bbf",  # oklch(65% 0.14 215) — steel blue
    Card.STRENGTH: "#c9920e",  # oklch(74% 0.16  55) — amber-gold
    Card.HERMIT: "#6b5fad",  # oklch(60% 0.10 265) — muted indigo
    Card.WHEEL_OF_FORTUNE: "#c9a010",  # oklch(74% 0.18  65) — golden
    Card.JUSTICE: "#2a72c2",  # oklch(63% 0.15 220) — medium blue
    Card.HANGED_MAN: "#1a9e9e",  # oklch(67% 0.16 185) — teal
    Card.DEATH: "#5c5ab5",  # oklch(62% 0.10 270) — indigo
    Card.TEMPERANCE: "#1ab8cc",  # oklch(70% 0.14 198) — near-cyan
    Card.DEVIL: "#b84820",  # oklch(58% 0.18  22) — dark red-orange
    Card.TOWER: "#cf7020",  # oklch(72% 0.18  40) — amber-orange
    Card.STAR: "#1ab0c8",  # oklch(74% 0.15 190) — sky cyan
    Card.MOON: "#7857bf",  # oklch(67% 0.18 285) — violet
    Card.SUN: "#d8b810",  # oklch(82% 0.17  82) — bright yellow
    Card.JUDGEMENT: "#ab5abf",  # oklch(66% 0.17 310) — purple
    Card.WORLD: "#22d3ee",  # var(--accent) ✦      — system cyan
}


def card_color(card: Card) -> str:
    """Return the hex accent color for a card."""
    return CARD_COLORS.get(card, ACCENT)


def card_styled(card: Card, label: str) -> str:
    """Wrap label in the card's accent color."""
    return f"[{card_color(card)}]{label}[/]"


# ---------------------------------------------------------------------------
# Status vocabulary  (§2 — fixed: running · thinking · idle · reversed)
# ---------------------------------------------------------------------------
def status_markup(status: str) -> str:
    """Rich markup string for an agent status dot + label."""
    s = status.lower()
    if s == "running":
        return f"[{GREEN}]● running[/]"
    if s == "thinking":
        return f"[{AMBER}]● thinking[/]"
    if s == "idle":
        return f"[{TXT3}]● idle[/]"
    if s == "reversed":
        return f"[bold {RED}]{REVERSED} reversed[/]"
    return f"[{TXT3}]{status}[/]"


# ---------------------------------------------------------------------------
# Inline markup helpers
# ---------------------------------------------------------------------------
def ok(msg: str) -> str:
    """✓ success in green."""
    return f"[bold {GREEN}]{CHECK} {msg}[/]"


def err(msg: str) -> str:
    """✗ error in red."""
    return f"[bold {RED}]{CROSS} {msg}[/]"


def warn(msg: str) -> str:
    """Warning in orange."""
    return f"[{ORANGE}]{msg}[/]"


def dim(msg: str) -> str:
    """Muted txt2 — secondary content."""
    return f"[{TXT2}]{msg}[/]"


def subtle(msg: str) -> str:
    """Subtle txt3 — placeholders, separators, rules."""
    return f"[{TXT3}]{msg}[/]"


def hl(msg: str) -> str:
    """Cyan accent — field labels, brand color."""
    return f"[{ACCENT}]{msg}[/]"


def cmd(msg: str) -> str:
    """Amber bold — arcana command name (the one place amber leads in the CLI)."""
    return f"[bold {AMBER}]{msg}[/]"


def flag(msg: str) -> str:
    """Accent-bright — CLI flags and options."""
    return f"[{ACCENT_BRIGHT}]{msg}[/]"


def card_ref(name: str) -> str:
    """Amber — card name referenced in output (same register as commands)."""
    return f"[{AMBER}]{name}[/]"


def eyebrow(label: str) -> str:
    """✦ prefix, uppercase — section eyebrow in cyan (mono register)."""
    return f"[bold {ACCENT}]{PROMPT} {label.upper()}[/]"


def prompt_line(command: str) -> str:
    """✦ cyan + amber command — styled CLI example prompt line."""
    return f"[{ACCENT}]{PROMPT}[/] [bold {AMBER}]{command}[/]"


def session_header(card_label: str, agent_name: str, model: str, session_id: str) -> str:
    """Chat session header for `arcana chat` (CLI Reference §Interactive Chat)."""
    return (
        f"\n[bold {ACCENT}]{PROMPT}  {card_label}[/]"
        f"  [{TXT3}]{SEP}[/]  "
        f"[bold {ACCENT}]{agent_name}[/]\n"
        f"[{TXT3}]{model}  {SEP}  Session #{session_id[:4]}[/]\n"
    )


# ---------------------------------------------------------------------------
# UI factories — pre-styled Rich objects
# ---------------------------------------------------------------------------
def make_table(title: str, *, show_header: bool = True) -> Table:
    """Standard data table: cyan title, txt3 headers, no row lines."""
    return Table(
        title=title,
        show_header=show_header,
        header_style=f"bold {TXT3}",
        border_style=TXT3,
        title_style=f"bold {ACCENT}",
        show_lines=False,
    )


def make_panel(content: RenderableType, *, title: str = "", card: Card | None = None) -> Panel:
    """Result panel. Border uses the card's accent hue if given, else cyan."""
    border = card_color(card) if card is not None else ACCENT
    return Panel(content, title=title, border_style=border)


def make_panel_fit(content: RenderableType, *, title: str = "", card: Card | None = None) -> Panel:
    """Compact fitted panel (expand=False)."""
    border = card_color(card) if card is not None else ACCENT
    return Panel(content, title=title, border_style=border, expand=False)


def make_error_panel(msg: str, *, title: str = "Error") -> Panel:
    """Red error panel."""
    return Panel(f"[bold {RED}]{CROSS} {msg}[/]", title=title, border_style=RED, expand=False)


# ---------------------------------------------------------------------------
# Card picker styles  (consumed by card_picker.py)
# ---------------------------------------------------------------------------
PICKER_CURSOR_SELECTED = f"bold reverse {GREEN}"
PICKER_CURSOR = f"bold reverse {ACCENT}"
PICKER_SELECTED = f"bold {GREEN}"
PICKER_BORDER = ACCENT
PICKER_BORDER_PREVIEW = TXT3
PICKER_HINT_FILTER = f"bold {AMBER}"
PICKER_HINT_DIM = TXT3
