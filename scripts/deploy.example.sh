#!/usr/bin/env bash
# Deploy compiled profiles to remote distribution server via strict artifact whitelist.
#
# CRITICAL SECURITY REQUIREMENT:
# Only sync the 3 client profiles. NEVER sync the entire dist/ directory,
# to avoid exposing local kernel runtime databases (cache.db, GeoSite.dat, etc.)
# or intermediate build state.

set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-user@example.com}"
REMOTE_PATH="${REMOTE_PATH:-/var/www/profiles}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$(cd "${SCRIPT_DIR}/../dist" && pwd)"

# Strict whitelist of artifacts to distribute
WHITELIST_ARTIFACTS=(
    "Clash-Verge-Rev.yaml"
    "shadowrocket.conf"
    "shadowrocket.yaml"
)

echo "[*] Synchronizing whitelisted distribution artifacts to ${REMOTE_HOST}:${REMOTE_PATH}..."

for artifact in "${WHITELIST_ARTIFACTS[@]}"; do
    src="${DIST_DIR}/${artifact}"
    if [[ -f "${src}" ]]; then
        size=$(wc -c < "${src}" | tr -d ' ')
        echo "  -> Uploading ${artifact} (${size} bytes)..."
        scp -p "${src}" "${REMOTE_HOST}:${REMOTE_PATH}/${artifact}"
    else
        echo "  [!] Skipping ${artifact} (not present in dist/)"
    fi
done

echo "[✓] Deployment completed safely (zero runtime databases exposed)."
