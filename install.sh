#!/bin/sh
# install.sh — installer for claude-bug-squash.
#
# Usage:
#   ./install.sh           install the package for normal use
#   ./install.sh --dev     editable install with dev extras (pytest)
#
# Strategy (first that works wins):
#   1. pipx install .            (isolated, recommended for a CLI tool)
#   2. pip install --user .      (per-user install)
#   3. python -m venv .venv      (local virtualenv fallback)
#
# The engine has ZERO runtime dependencies (stdlib only), so this is fast.
# The fixer + review panel shell out to the `claude` CLI (Claude Code), which
# you install separately — we WARN if it's missing but do not fail.
#
# Idempotent: safe to re-run.
set -e

DEV=0
for arg in "$@"; do
  case "$arg" in
    --dev) DEV=1 ;;
    -h|--help)
      echo "Usage: ./install.sh [--dev]"
      echo "  --dev   editable install with dev extras (pip install -e \".[dev]\")"
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $arg" >&2
      echo "Usage: ./install.sh [--dev]" >&2
      exit 2
      ;;
  esac
done

# Run from the repo root (where this script lives) so "." resolves correctly.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

if [ ! -f pyproject.toml ]; then
  echo "ERROR: pyproject.toml not found in $SCRIPT_DIR." >&2
  echo "       Run this script from the claude-bug-squash repo root." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 1. Find a Python >= 3.11
# ---------------------------------------------------------------------------
find_python() {
  for cand in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' >/dev/null 2>&1; then
        echo "$cand"
        return 0
      fi
    fi
  done
  return 1
}

PY=$(find_python) || {
  echo "ERROR: no Python >= 3.11 found on PATH." >&2
  echo "       claude-bug-squash requires Python 3.11 or newer." >&2
  echo "       Install it from https://www.python.org/downloads/ (or your package manager) and re-run." >&2
  exit 1
}
PYVER=$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
echo "✓ Python $PYVER  ($PY)"

# ---------------------------------------------------------------------------
# 2. Warn (do not fail) if the `claude` CLI is absent
# ---------------------------------------------------------------------------
if command -v claude >/dev/null 2>&1; then
  echo "✓ claude CLI found ($(command -v claude))"
else
  echo "⚠ WARNING: the 'claude' CLI (Claude Code) was not found on PATH."
  echo "  bug-squash can still triage and gate, but the FIXER and the REVIEW PANEL"
  echo "  shell out to 'claude' — they will not run without it."
  echo "  Install it from https://docs.anthropic.com/en/docs/claude-code and ensure"
  echo "  'claude' is on your PATH."
fi

if [ "$DEV" -eq 1 ]; then
  SPEC='-e .[dev]'
  LABEL='editable + dev extras'
else
  SPEC='.'
  LABEL='package'
fi

# ---------------------------------------------------------------------------
# 3. Install — pipx → pip --user → venv
# ---------------------------------------------------------------------------
INSTALL_BIN=""        # absolute path to the installed `bug-squash`, when known
INSTALL_METHOD=""

try_pipx() {
  command -v pipx >/dev/null 2>&1 || return 1
  # pipx installs a CLI into its own isolated venv. Editable + extras are
  # supported via --editable / --pip-args; "--force" makes it idempotent.
  echo "→ Installing via pipx ($LABEL)..."
  if [ "$DEV" -eq 1 ]; then
    pipx install --force --editable ".[dev]" || return 1
  else
    pipx install --force "." || return 1
  fi
  INSTALL_METHOD="pipx"
  return 0
}

try_pip_user() {
  echo "→ Installing via pip --user ($LABEL)..."
  # shellcheck disable=SC2086
  "$PY" -m pip install --user --upgrade $SPEC || return 1
  INSTALL_METHOD="pip --user"
  return 0
}

try_venv() {
  echo "→ Falling back to a local virtualenv at .venv ($LABEL)..."
  "$PY" -m venv .venv || return 1
  # shellcheck disable=SC1091
  . .venv/bin/activate
  python -m pip install --upgrade pip >/dev/null 2>&1 || true
  # shellcheck disable=SC2086
  python -m pip install $SPEC || return 1
  INSTALL_BIN="$SCRIPT_DIR/.venv/bin/bug-squash"
  INSTALL_METHOD="venv (.venv)"
  return 0
}

if try_pipx; then :
elif try_pip_user; then :
elif try_venv; then :
else
  echo "ERROR: all install methods failed (pipx, pip --user, venv)." >&2
  echo "       Try manually:  $PY -m pip install $SPEC" >&2
  exit 1
fi
echo "✓ Installed via $INSTALL_METHOD"

# ---------------------------------------------------------------------------
# 4. Verify `bug-squash --version`
# ---------------------------------------------------------------------------
BUG_SQUASH=""
if [ -n "$INSTALL_BIN" ] && [ -x "$INSTALL_BIN" ]; then
  BUG_SQUASH="$INSTALL_BIN"
elif command -v bug-squash >/dev/null 2>&1; then
  BUG_SQUASH=$(command -v bug-squash)
else
  # pip --user often drops the script in a per-user bin not yet on PATH.
  USER_BASE=$("$PY" -m site --user-base 2>/dev/null || true)
  if [ -n "$USER_BASE" ] && [ -x "$USER_BASE/bin/bug-squash" ]; then
    BUG_SQUASH="$USER_BASE/bin/bug-squash"
  fi
fi

if [ -n "$BUG_SQUASH" ] && "$BUG_SQUASH" --version >/dev/null 2>&1; then
  echo "✓ $("$BUG_SQUASH" --version)"
else
  echo "⚠ WARNING: 'bug-squash' was installed but is not on your PATH yet."
  if [ "$INSTALL_METHOD" = "venv (.venv)" ]; then
    echo "  Activate the venv first:  . .venv/bin/activate"
  elif [ "$INSTALL_METHOD" = "pip --user" ]; then
    USER_BASE=$("$PY" -m site --user-base 2>/dev/null || echo "\$HOME/.local")
    echo "  Add the user bin dir to PATH:  export PATH=\"$USER_BASE/bin:\$PATH\""
  elif [ "$INSTALL_METHOD" = "pipx" ]; then
    echo "  Ensure pipx's bin dir is on PATH:  pipx ensurepath  (then restart your shell)"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Next steps
# ---------------------------------------------------------------------------
echo ""
echo "Next steps:"
echo "  1. cd into the project you want to bug-squash"
echo "  2. run:  bug-squash init      (writes bugsquash.toml + review agents)"
echo "  3. run:  bug-squash add \"<the bug>\"   then   bug-squash run"
echo ""
echo "Auto-merge stays OFF by default. bug-squash only proposes + gates until you"
echo "explicitly arm it (AUTO_SQUASH_SHADOW=0 + AUTO_SQUASH_MERGE_ARMED=1). See README."
