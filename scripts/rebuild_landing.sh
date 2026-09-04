#!/usr/bin/env bash
# PHASE 26: rebuild the landing SPA from the source repo and copy
# the built bundle into elyasmin's static tree.
#
# Run this after pulling new commits from
# https://github.com/Abdelhammid1/landing-yasmin
#
# Usage:
#   bash scripts/rebuild_landing.sh
#
# Environment overrides (optional):
#   LANDING_SRC=/path/to/landing-yasmin   # default: /tmp/landing-yasmin
set -euo pipefail

LANDING_SRC="${LANDING_SRC:-/tmp/landing-yasmin}"
ELYASMIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${ELYASMIN_ROOT}/app/static/landing"

if [ ! -d "$LANDING_SRC" ]; then
  echo "Cloning landing-yasmin into ${LANDING_SRC}…"
  git clone --depth 1 https://github.com/Abdelhammid1/landing-yasmin "$LANDING_SRC"
else
  echo "Pulling latest into ${LANDING_SRC}…"
  ( cd "$LANDING_SRC" && git pull --ff-only )
fi

# Vite config must have base='/static/landing/' — the first time you
# run this the file may not have it; the sed line below is idempotent.
if ! grep -q "base: '/static/landing/'" "${LANDING_SRC}/vite.config.ts"; then
  echo "Patching vite.config.ts to set base='/static/landing/'…"
  sed -i "s|return {|return {\n    base: '/static/landing/',|" "${LANDING_SRC}/vite.config.ts"
fi

( cd "$LANDING_SRC" && npm install --silent && npx vite build )

echo "Copying built assets → ${DEST}"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "${LANDING_SRC}/dist/"* "$DEST/"

echo "Done. Restart the Flask server and hit / to see the new landing."
