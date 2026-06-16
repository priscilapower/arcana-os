#!/bin/sh
# Arcana OS installer — "The OS that gives your agents a soul."
#
#   curl -LsSf https://arcanaos.cloud/install.sh | sh
#
# Installs the `arcana` CLI in an isolated environment. Requires no
# pre-existing Python: it bootstraps uv, which fetches a managed Python and
# installs the tool. macOS / Linux. (Windows users: see the docs.)
set -eu

cyan()  { printf '\033[36m%s\033[0m\n' "$*"; }
amber() { printf '\033[33m%s\033[0m\n' "$*"; }
fail()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || fail "curl is required but not found."

# 1. Ensure uv is available — it manages the isolated env and Python for us.
if ! command -v uv >/dev/null 2>&1; then
  cyan "Installing uv (Python toolchain manager)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # uv installs to ~/.local/bin by default; expose it to this session.
  PATH="${XDG_BIN_HOME:-$HOME/.local/bin}:$HOME/.local/bin:$PATH"
  export PATH
fi

command -v uv >/dev/null 2>&1 || fail \
  "uv was installed but isn't on PATH yet. Open a new terminal and re-run this command."

# 2. Install (or upgrade) the Arcana OS CLI.
cyan "Installing arcana-os…"
uv tool install --upgrade arcana-os

# 3. Make sure the tool bin dir is on PATH for future shells.
uv tool update-shell >/dev/null 2>&1 || true

cyan "✓ Arcana installed."
if command -v arcana >/dev/null 2>&1; then
  printf '\n  Next:  %s\n\n' "arcana init"
else
  amber "Restart your shell (or run 'uv tool update-shell'), then: arcana init"
fi
