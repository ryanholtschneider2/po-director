#!/usr/bin/env bash
# Reinstall the `po` tool env with ALL ADE editable packs in one atomic
# `uv tool install`.
#
# Why this exists: `uv tool install <core> --with-editable <X>` REPLACES the
# tool's editable set every call, so installing one pack on its own silently
# evicts the others ("po director" / "pr-sheriff" / "epic" vanish). `po packs
# install` aggregates correctly, but a stray raw `uv tool install --with-editable`
# or `uv pip install -e` bypasses it. This script is the canonical repair: it
# declares the full set and installs them together, so nothing can evict anything.
#
# Run it after adding/moving a pack, or whenever `po director` / a formula is
# missing from `po list`.
set -euo pipefail

P="${ADE_CODE_ROOT:-$HOME/Desktop/Code/personal}"

# Canonical ADE editable packs (core first). Add new packs here.
CORE="$P/prefect-orchestration"
PACKS=(
  "$P/po-formulas-software-dev/parent"
  "$P/po-formulas-software-dev/wts"
  "$P/po-director"
)

argv=(tool install --reinstall --editable "$CORE")
for pack in "${PACKS[@]}"; do
  if [[ -f "$pack/pyproject.toml" ]]; then
    argv+=(--with-editable "$pack")
  else
    echo "warning: skipping missing pack $pack" >&2
  fi
done

echo "uv ${argv[*]}"
uv "${argv[@]}"

echo
echo "Installed. Verifying key entry points:"
for want in "director" "pr-sheriff" "epic" "software-dev-full"; do
  if po list 2>/dev/null | grep -q "\b${want}\b"; then
    echo "  ok   $want"
  else
    echo "  MISSING $want — check the pack's pyproject [project.entry-points]" >&2
  fi
done
