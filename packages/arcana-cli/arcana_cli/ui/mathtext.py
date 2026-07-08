"""LaTeX-lite → Unicode normaliser for terminal rendering.

Terminals can't typeset math, but local models emit it constantly. This is a
light normaliser, not an engine: it unwraps inline math and maps the common
symbols/styles to Unicode, and leaves anything it can't convert cleanly
(fractions, sums with limits, matrices) as raw TeX. :func:`normalize_math` is
the only entry point — it rewrites inline/display math spans in a block of text
while leaving code spans and fences untouched.
"""

import re
from collections.abc import Callable

_TEX_SYMBOLS = {
    "times": "×",
    "cdot": "·",
    "div": "÷",
    "pm": "±",
    "mp": "∓",
    "ast": "∗",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ne": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "∼",
    "propto": "∝",
    "cong": "≅",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "leftrightarrow": "↔",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "Leftrightarrow": "⇔",
    "mapsto": "↦",
    "infty": "∞",
    "partial": "∂",
    "nabla": "∇",
    "sqrt": "√",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "oint": "∮",
    "forall": "∀",
    "exists": "∃",
    "in": "∈",
    "notin": "∉",
    "ni": "∋",
    "subset": "⊂",
    "subseteq": "⊆",
    "supset": "⊃",
    "supseteq": "⊇",
    "cup": "∪",
    "cap": "∩",
    "emptyset": "∅",
    "setminus": "∖",
    "wedge": "∧",
    "vee": "∨",
    "neg": "¬",
    "oplus": "⊕",
    "otimes": "⊗",
    "cdots": "⋯",
    "ldots": "…",
    "dots": "…",
    "vdots": "⋮",
    "langle": "⟨",
    "rangle": "⟩",
    "star": "⋆",
    "circ": "∘",
    "bullet": "•",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
}
_SUPERSCRIPT = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
    "+": "⁺",
    "-": "⁻",
    "=": "⁼",
    "(": "⁽",
    ")": "⁾",
    "n": "ⁿ",
    "i": "ⁱ",
}
_SUBSCRIPT = {
    "0": "₀",
    "1": "₁",
    "2": "₂",
    "3": "₃",
    "4": "₄",
    "5": "₅",
    "6": "₆",
    "7": "₇",
    "8": "₈",
    "9": "₉",
    "+": "₊",
    "-": "₋",
    "=": "₌",
    "(": "₍",
    ")": "₎",
    "a": "ₐ",
    "e": "ₑ",
    "o": "ₒ",
    "x": "ₓ",
    "h": "ₕ",
    "k": "ₖ",
    "l": "ₗ",
    "m": "ₘ",
    "n": "ₙ",
    "p": "ₚ",
    "s": "ₛ",
    "t": "ₜ",
    "i": "ᵢ",
    "j": "ⱼ",
    "r": "ᵣ",
    "u": "ᵤ",
    "v": "ᵥ",
}
# Double-struck letters that live in the BMP (the Plane-1 block skips them).
_BB_EXCEPT = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}


def _to_mathbf(s: str) -> str:
    """Map ASCII letters/digits to their Unicode mathematical-bold forms."""
    out: list[str] = []
    for ch in s:
        if "A" <= ch <= "Z":
            out.append(chr(0x1D400 + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D41A + ord(ch) - 97))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7CE + ord(ch) - 48))
        else:
            out.append(ch)
    return "".join(out)


def _to_mathbb(s: str) -> str:
    """Map ASCII letters/digits to Unicode double-struck (blackboard) forms."""
    out: list[str] = []
    for ch in s:
        if ch in _BB_EXCEPT:
            out.append(_BB_EXCEPT[ch])
        elif "A" <= ch <= "Z":
            out.append(chr(0x1D538 + ord(ch) - 65))
        elif "a" <= ch <= "z":
            out.append(chr(0x1D552 + ord(ch) - 97))
        elif "0" <= ch <= "9":
            out.append(chr(0x1D7D8 + ord(ch) - 48))
        else:
            out.append(ch)
    return "".join(out)


_MATHBF_RE = re.compile(r"\\mathbf\{([^{}]*)\}")
_MATHBB_RE = re.compile(r"\\mathbb\{([^{}]*)\}")
# Style wrappers with no Unicode equivalent — keep the inner text, drop the macro.
_STRIP_WRAP_RE = re.compile(
    r"\\(?:mathrm|mathit|mathsf|mathtt|mathcal|mathscr|mathnormal|text|textbf|textit|boldsymbol|operatorname)\{([^{}]*)\}"
)
_SUP_RE = re.compile(r"\^\{([^{}]*)\}|\^(\S)")
_SUB_RE = re.compile(r"_\{([^{}]*)\}|_(\S)")
_SYM_RE = re.compile(r"\\([a-zA-Z]+)")
_SPACING_RE = re.compile(r"\\[,;:!> ]|\\quad|\\qquad")


def _script_repl(table: dict[str, str]) -> "Callable[[re.Match[str]], str]":
    def repl(m: re.Match[str]) -> str:
        body = m.group(1) if m.group(1) is not None else m.group(2)
        if body and all(c in table for c in body):
            return "".join(table[c] for c in body)
        return m.group(0)  # unmappable → leave as-is (braces, if any, force fallback)

    return repl


def _tex_to_unicode(inner: str) -> str:
    s = _SPACING_RE.sub(" ", inner).replace("\\left", "").replace("\\right", "")
    s = _MATHBF_RE.sub(lambda m: _to_mathbf(m.group(1)), s)
    s = _MATHBB_RE.sub(lambda m: _to_mathbb(m.group(1)), s)
    s = _STRIP_WRAP_RE.sub(lambda m: m.group(1), s)
    s = _SYM_RE.sub(lambda m: _TEX_SYMBOLS.get(m.group(1), m.group(0)), s)
    s = _SUP_RE.sub(_script_repl(_SUPERSCRIPT), s)
    s = _SUB_RE.sub(_script_repl(_SUBSCRIPT), s)
    return s


def _looks_like_math(inner: str) -> bool:
    """Whether a ``$...$`` span is math rather than prose (e.g. a ``$5 and $6`` price)."""
    if any(c in inner for c in "\\^_"):
        return True
    stripped = inner.strip()
    return bool(stripped) and " " not in stripped and "\t" not in stripped and not stripped.isdigit()


_MATH_RE = re.compile(r"\$\$(?P<d>.+?)\$\$|\\\[(?P<b>.+?)\\\]|\\\((?P<p>.+?)\\\)|\$(?P<i>.+?)\$", re.DOTALL)
_CODE_RE = re.compile(r"```.*?```|``.*?``|`[^`]*`", re.DOTALL)


def _math_repl(m: re.Match[str]) -> str:
    inline = m.group("i")
    inner = m.group("d") or m.group("b") or m.group("p") or inline
    if inline is not None and not _looks_like_math(inner):
        return m.group(0)  # prose like "$5 and $6" — leave the dollars alone
    converted = _tex_to_unicode(inner)
    if any(c in converted for c in "\\{}"):
        return m.group(0)  # couldn't fully convert — keep the raw TeX
    return converted


def normalize_math(text: str) -> str:
    """Convert inline LaTeX to Unicode, leaving code spans/fences untouched."""
    out: list[str] = []
    last = 0
    for code in _CODE_RE.finditer(text):
        out.append(_MATH_RE.sub(_math_repl, text[last : code.start()]))
        out.append(code.group(0))
        last = code.end()
    out.append(_MATH_RE.sub(_math_repl, text[last:]))
    return "".join(out)
