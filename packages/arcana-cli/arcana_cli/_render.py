"""Output contract for the scriptable tool / MCP command groups.

Human output is Rich tables (see :mod:`arcana_cli.ui.theme`); this module owns
the two machine-facing halves of the contract that scripts — and, later, the
server surface — lean on: a stable JSON emitter and a uniform exit-code
vocabulary. Keeping them in one place is what lets every new command render the
same way and fail with the same codes.
"""

import json
from typing import Any

# Uniform exit codes. A read command or a successful action returns OK; every
# failure maps to one of three buckets a caller can branch on without parsing
# the human message.
EXIT_OK = 0
EXIT_ERROR = 1  # generic failure — bad args, unreachable server, transport error
EXIT_NOT_FOUND = 2  # a named server / tool / agent does not exist
EXIT_DENIED = 3  # the tool exists but is withheld (changed / unapproved)


def emit_json(data: Any) -> None:
    """Print ``data`` as a JSON document — the scripting contract.

    ``default=str`` renders UUIDs, datetimes, and enums (``StrEnum`` values are
    already ``str``) as strings, so a command can pass plain dicts/lists built
    from the core models without a bespoke encoder.
    """
    print(json.dumps(data, indent=2, default=str))


def truncate(text: str, width: int = 60) -> str:
    """Collapse whitespace and clip ``text`` to ``width`` chars for a table cell."""
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"
