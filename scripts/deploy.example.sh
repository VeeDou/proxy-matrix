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
# SECRET_SUBDIR must be set to an unguessable token directory (e.g., openssl rand -hex 32)
SECRET_SUBDIR="${SECRET_SUBDIR:-token-path-placeholder-change-to-64-hex-token}"

if [[ "${SECRET_SUBDIR}" == "token-path-placeholder-change-to-64-hex-token" || -z "${SECRET_SUBDIR}" ]]; then
    echo "[!] ERROR: SECRET_SUBDIR is unset or using default placeholder."
    echo "    Please set SECRET_SUBDIR to an unguessable 64-hex token path to protect client profiles."
    echo "    Example: export SECRET_SUBDIR=\$(openssl rand -hex 32)"
    exit 1
fi

if [[ ! "${SECRET_SUBDIR}" =~ ^[0-9a-fA-F]{32,64}$ ]]; then
    echo "[!] ERROR: SECRET_SUBDIR must be a 32-64 hex character unguessable token."
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$(cd "${SCRIPT_DIR}/../dist" && pwd)"
TARGET_DIR="${REMOTE_PATH}/${SECRET_SUBDIR}"

# Strict whitelist of artifacts to distribute
WHITELIST_ARTIFACTS=(
    "Clash-Verge-Rev.yaml"
    "shadowrocket.conf"
    "shadowrocket.yaml"
)

echo "[*] Synchronizing whitelisted distribution artifacts to ${REMOTE_HOST}:${TARGET_DIR}..."
ssh "${REMOTE_HOST}" "mkdir -p '${TARGET_DIR}'"

for artifact in "${WHITELIST_ARTIFACTS[@]}"; do
    src="${DIST_DIR}/${artifact}"
    if [[ -f "${src}" ]]; then
        size=$(wc -c < "${src}" | tr -d ' ')
        tmp_target="${TARGET_DIR}/${artifact}.tmp.$$"
        final_target="${TARGET_DIR}/${artifact}"
        echo "  -> Atomic uploading ${artifact} (${size} bytes)..."
        # 1. Upload to temporary file
        scp "${src}" "${REMOTE_HOST}:${tmp_target}"
        # 2. Set readable permissions (0644 for web server) and atomically replace target
        ssh "${REMOTE_HOST}" "chmod 0644 '${tmp_target}' && mv -f '${tmp_target}' '${final_target}'"
    else
        echo "  [!] Skipping ${artifact} (not present in dist/)"
    fi
done

echo "[✓] Deployment completed safely (zero runtime databases exposed, atomic replacement verified)."
