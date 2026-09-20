#!/usr/bin/env bash
# rainge installer: link this repo as an OMP plugin
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

need() { command -v "$1" >/dev/null || { echo "missing: $1 ($2)" >&2; exit 1; }; }
need omp "the OMP CLI on PATH"
need python3 "bus and panel run on stdlib python"
need tmux "member seating spawns one tmux window per alias"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "missing: python3 >= 3.10 ($(python3 --version 2>&1))" >&2; exit 1; }

# Remove legacy raw installs: they would double-register /rainge.
rm -f "${HOME}/.omp/agent/extensions/rainge.ts" "${HOME}/.omp/agent/skills/rainge/SKILL.md"
rmdir "${HOME}/.omp/agent/skills/rainge" 2>/dev/null || true

omp plugin link "$here"
