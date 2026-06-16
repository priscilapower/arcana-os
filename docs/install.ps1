# Arcana OS installer (Windows) — "The OS that gives your agents a soul."
#
#   powershell -ExecutionPolicy ByPass -c "irm https://arcanaos.cloud/install.ps1 | iex"
#
# Installs the `arcana` CLI in an isolated environment. Requires no
# pre-existing Python: it bootstraps uv, which fetches a managed Python and
# installs the tool. (macOS / Linux users: use install.sh.)

$ErrorActionPreference = 'Stop'

function Info($m) { Write-Host $m -ForegroundColor Cyan }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }

# 1. Ensure uv is available — it manages the isolated env and Python for us.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Info "Installing uv (Python toolchain manager)..."
    # Run inline (same session) so uv's PATH update applies here.
    irm https://astral.sh/uv/install.ps1 | iex
    $uvBin = Join-Path $env:USERPROFILE ".local\bin"
    if ((Test-Path $uvBin) -and ($env:Path -notlike "*$uvBin*")) {
        $env:Path = "$uvBin;$env:Path"
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Warn "uv was installed but isn't on PATH yet. Open a new terminal and re-run this command."
    exit 1
}

# 2. Install (or upgrade) the Arcana OS CLI.
Info "Installing arcana-os..."
uv tool install --upgrade arcana-os

# 3. Make sure the tool bin dir is on PATH for future shells.
uv tool update-shell *> $null

Info "Arcana installed."
if (Get-Command arcana -ErrorAction SilentlyContinue) {
    Write-Host ""
    Write-Host "  Next:  arcana init"
    Write-Host ""
} else {
    Warn "Restart your shell, then: arcana init"
}
