"""Turn a fetched response body into text an LLM can read.

Best-effort and dependency-free: HTML is reduced to its main text with the
standard-library parser (drop ``script`` / ``style`` / etc., keep block breaks,
collapse whitespace); ``text/*`` and JSON pass through decoded; anything else
returns a short typed placeholder rather than raw bytes. Full HTML→Markdown
fidelity, boilerplate removal, and JS rendering are explicitly out of scope.
"""

import re
from html.parser import HTMLParser

# Tags whose text content is never page content.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head", "svg", "iframe"})

# Tags that imply a line break around their content, so extracted text keeps
# some structure instead of collapsing into one run.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "hr",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "thead",
        "tbody",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "figure",
    }
)

# Whitespace inside a text node — including source newlines — collapses to a
# single space (HTML rendering rules), so the only real line breaks are the ones
# block tags insert.
_ANY_WS = re.compile(r"\s+")
# A run of whitespace containing at least one newline collapses to one newline,
# folding adjacent block boundaries (``</div><div>``) into a single break.
_NEWLINE_RUN = re.compile(r"\s*\n\s*")
_SPACES = re.compile(r"[ \t\f\r]+")


class _TextExtractor(HTMLParser):
    """Collects readable text, skipping non-content tags and marking block breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(_ANY_WS.sub(" ", data))

    def text(self) -> str:
        joined = "".join(self._parts)
        joined = _NEWLINE_RUN.sub("\n", joined)
        joined = _SPACES.sub(" ", joined)
        return joined.strip()


def _mime_of(content_type: str) -> str:
    """The bare ``type/subtype`` from a Content-Type header, lowercased."""
    return content_type.split(";", 1)[0].strip().lower()


def _decode(content: bytes, encoding: str | None) -> str:
    """Decode bytes with the response's charset, tolerating bad bytes."""
    for enc in (encoding, "utf-8"):
        if enc:
            try:
                return content.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
    return content.decode("utf-8", errors="replace")


def _is_texty(mime: str) -> bool:
    """True for MIME types safe to hand back as decoded text."""
    return (
        mime.startswith("text/")
        or mime == "application/json"
        or mime == "application/xml"
        or mime.endswith("+json")
        or mime.endswith("+xml")
    )


def extract_text(content: bytes, content_type: str, encoding: str | None) -> str:
    """Return readable text for a body, or a typed placeholder for binary content.

    HTML is reduced to main text; other textual/JSON types are decoded as-is;
    an unsupported (binary) type yields ``(unsupported content type: …)`` with no
    body, so raw bytes never reach the model.
    """
    mime = _mime_of(content_type)
    if mime in {"text/html", "application/xhtml+xml"}:
        parser = _TextExtractor()
        parser.feed(_decode(content, encoding))
        parser.close()
        return parser.text()
    if _is_texty(mime):
        return _decode(content, encoding)
    return f"(unsupported content type: {mime or 'unknown'})"
