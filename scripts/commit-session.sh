#!/usr/bin/env bash
# commit-session.sh — stage and commit all project changes at end of a build session.
#
# Usage:
#   ./scripts/commit-session.sh              # auto-generate message from handoff doc + diff stat
#   ./scripts/commit-session.sh "My message" # override with explicit message
#
# In Claude Code:
#   ! ./scripts/commit-session.sh
#
# What it does:
#   1. Stages all changes (respects .gitignore — no .env, .venv, __pycache__)
#   2. Generates a descriptive message from the handoff doc's "Last Updated" line + diff stat
#   3. Commits. Does NOT push — that stays a deliberate manual step.
#
# Exits 0 with a message when nothing is staged.
# Never pushes, never force-resets, never touches remotes.

set -uo pipefail

# ── Locate repo root ──────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null || true)"

if [ -z "$REPO_ROOT" ]; then
  echo "commit-session: error — not inside a git repository" >&2
  exit 1
fi

cd "$REPO_ROOT"

# ── Check for changes ─────────────────────────────────────────────────────────

UNTRACKED=$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')
MODIFIED=$(git diff --name-only | wc -l | tr -d ' ')
STAGED=$(git diff --cached --name-only | wc -l | tr -d ' ')

if [ "$UNTRACKED" -eq 0 ] && [ "$MODIFIED" -eq 0 ] && [ "$STAGED" -eq 0 ]; then
  echo "commit-session: nothing to commit — working tree is clean"
  exit 0
fi

# ── Stage everything (respects .gitignore) ────────────────────────────────────

git add -A

# ── Build commit message ──────────────────────────────────────────────────────

if [ -n "${1:-}" ]; then
  # Explicit override passed as argument
  MSG="$1"
else
  # Auto-generate: try to pull the slice description from the handoff doc.
  HANDOFF="docs/handoff/current-state.md"
  SLICE_DESC=""

  if [ -f "$HANDOFF" ]; then
    # The handoff doc format:
    #   ## Last Updated
    #   2026-04-01 — Slack operator surface complete (Slice 13)
    SLICE_DESC=$(awk '/^## Last Updated/{found=1; next} found && /^[0-9]/{print; exit}' "$HANDOFF" | sed 's/^[[:space:]]*//')
  fi

  # Get diff stat summary (last line: "N files changed, +X/-Y")
  STAT=$(git diff --cached --stat 2>/dev/null | tail -1 | sed 's/^[[:space:]]*//')

  if [ -n "$SLICE_DESC" ] && [ -n "$STAT" ]; then
    MSG="${SLICE_DESC} — ${STAT}"
  elif [ -n "$SLICE_DESC" ]; then
    MSG="${SLICE_DESC}"
  else
    # Fallback: list top changed files + stat
    FILES=$(git diff --cached --name-only | head -4 | tr '\n' ', ' | sed 's/,$//')
    MSG="Auto-commit: ${FILES}${STAT:+ — $STAT}"
  fi
fi

# ── Commit ────────────────────────────────────────────────────────────────────

git commit -m "$(printf '%s\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>' "$MSG")"

echo ""
echo "✓ commit-session: $(git log --oneline -1)"
