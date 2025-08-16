#!/usr/bin/env bash
set -euo pipefail

# Generate qlib bin tarball and upload it as a GitHub release asset.
# Requires a GitHub personal access token in $GITHUB_PAT (or $GH_TOKEN / $GITHUB_TOKEN).

TOKEN="${GITHUB_PAT:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
if [[ -z "${TOKEN}" ]]; then
  echo "Error: GITHUB_PAT environment variable is not set." >&2
  exit 1
fi

# Determine repository (owner/repo)
REPO="${GITHUB_REPOSITORY:-}" 
if [[ -z "${REPO}" ]]; then
  # Try to parse from git config
  ORIGIN_URL=$(git config --get remote.origin.url)
  REPO=$(echo "$ORIGIN_URL" | sed -n 's#.*github.com[/:]\(.*\)\.git#\1#p')
fi
if [[ -z "${REPO}" ]]; then
  echo "Error: could not determine repository." >&2
  exit 1
fi

DATE=$(date +%F)
ASSET_NAME="qlib_bin.tar.gz"
BODY="Daily update release"

# Run dump script to generate the tarball
bash dump_qlib_bin.sh

FILE_PATH="$(pwd)/${ASSET_NAME}"
if [[ ! -f "${FILE_PATH}" ]]; then
  echo "Error: ${FILE_PATH} not found" >&2
  exit 1
fi

API="https://api.github.com/repos/${REPO}"
AUTH_HEADER="Authorization: token ${TOKEN}"

# Get or create release
RELEASE_ID=$(curl -fsSL -H "${AUTH_HEADER}" "${API}/releases/tags/${DATE}" | jq -r '.id' 2>/dev/null || true)
if [[ -z "${RELEASE_ID}" || "${RELEASE_ID}" == "null" ]]; then
  RELEASE_ID=$(curl -fsSL -H "${AUTH_HEADER}" -H 'Content-Type: application/json' \
    -d "{\"tag_name\":\"${DATE}\",\"name\":\"${DATE}\",\"body\":\"${BODY}\"}" \
    "${API}/releases" | jq -r '.id')
fi

if [[ -z "${RELEASE_ID}" || "${RELEASE_ID}" == "null" ]]; then
  echo "Error: unable to create or fetch release." >&2
  exit 1
fi

# Delete existing asset if present
ASSET_ID=$(curl -fsSL -H "${AUTH_HEADER}" "${API}/releases/${RELEASE_ID}/assets" | jq -r \
  ".[] | select(.name==\"${ASSET_NAME}\") | .id")
if [[ -n "${ASSET_ID}" ]]; then
  curl -fsSL -X DELETE -H "${AUTH_HEADER}" "${API}/releases/assets/${ASSET_ID}" >/dev/null
fi

# Upload new asset
UPLOAD_URL=$(curl -fsSL -H "${AUTH_HEADER}" "${API}/releases/${RELEASE_ID}" | jq -r '.upload_url' | sed 's/{?name,label}//')

curl -fsSL -H "${AUTH_HEADER}" -H "Content-Type: application/gzip" \
  --data-binary "@${FILE_PATH}" "${UPLOAD_URL}?name=${ASSET_NAME}" >/dev/null

echo "Uploaded ${ASSET_NAME} to release ${DATE}"
