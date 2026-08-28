#!/bin/bash
# Builds the Lovelace card and deploys the integration to the Home Assistant
# instance.
#
# Configuration comes from .env (see .env.example); environment variables that
# are already set take precedence.

set -e

cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

HOST="${HA_HOST:-}"
CONFIG="${HA_CONFIG:-/config}"
TARGET="${HA_TARGET:-${CONFIG}/custom_components}"
SSH_PORT="${HA_SSH_PORT:-22}"

if [ -z "$HOST" ]; then
  echo "HA_HOST is not set. Copy .env.example to .env and fill it in." >&2
  exit 1
fi

echo "Building card ..."
npm --prefix card ci --silent
# The local build counter is opt-in, so only the bundle deployed from here
# carries one; releases built on GitHub stay at the plain semver.
CROWDSEC_BUILD_COUNTER=1 npm --prefix card run build

echo "Deploying integration to ${HOST}:${TARGET} ..."
rsync -az --delete \
  -e "ssh -p ${SSH_PORT}" \
  --exclude '__pycache__' \
  custom_components/crowdsec "${HOST}:${TARGET}/"

# Read back what the build baked into the bundle, so the message below names
# the exact build that was just deployed.
VERSION="$(node -p "require('./card/package.json').version")+build.$(cat card/.build-number 2>/dev/null || echo '?')"

echo "Done. Deployed card version: ${VERSION}"
echo "Restart Home Assistant so the integration is reloaded."
echo "The integration serves the card itself; the console line of the reloaded"
echo "dashboard has to show the version above — an older one is a cached bundle."
