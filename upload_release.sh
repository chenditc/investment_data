#!/usr/bin/env bash
set -euo pipefail

# Configure git proxy if http_proxy or https_proxy are set
if [[ -n "${http_proxy-}" ]]; then
  git config --global http.proxy "${http_proxy}"
fi
if [[ -n "${https_proxy-}" ]]; then
  git config --global https.proxy "${https_proxy}"
fi

# Generate qlib bin tarball and upload it as a GitHub release asset.
# Requires a GitHub personal access token in $GITHUB_PAT (or $GH_TOKEN / $GITHUB_TOKEN).

TOKEN="${GITHUB_PAT:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
if [[ -z "${TOKEN}" ]]; then
  echo "Error: GITHUB_PAT environment variable is not set." >&2
  exit 1
fi

# Determine repository (owner/repo)
REPO="${REPO:-${GITHUB_REPOSITORY:-chenditc/investment_data}}"

DATE="${DATE:-$(date +%F)}"
ASSET_NAME="qlib_bin.tar.gz"
BODY="Daily update release"

# Ensure jq is available
if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required but not installed." >&2
  exit 1
fi

# Run dump script to generate the tarball. Release uploads should fail closed
# when the source data or generated archive is stale.
CHECK_FRESHNESS="${CHECK_FRESHNESS:-1}" bash dump_qlib_bin.sh

FILE_PATH="$(pwd)/${ASSET_NAME}"
if [[ ! -f "${FILE_PATH}" ]]; then
  echo "Error: ${FILE_PATH} not found" >&2
  exit 1
fi

API="https://api.github.com/repos/${REPO}"
AUTH_HEADER="Authorization: token ${TOKEN}"

# Get or create release
RELEASE_ID=$(curl -sSL -H "${AUTH_HEADER}" "${API}/releases/tags/${DATE}" | jq -r '.id' 2>/dev/null || true)
if [[ -z "${RELEASE_ID}" || "${RELEASE_ID}" == "null" ]]; then
  RELEASE_ID=$(curl -fsSL --retry 3 --retry-delay 10 -H "${AUTH_HEADER}" -H 'Content-Type: application/json' \
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

# Upload new asset with retry
UPLOAD_URL=$(curl -fsSL -H "${AUTH_HEADER}" "${API}/releases/${RELEASE_ID}" | jq -r '.upload_url' | sed 's/{?name,label}//')

MAX_RETRIES=3
for attempt in $(seq 1 ${MAX_RETRIES}); do
  echo "Upload attempt ${attempt}/${MAX_RETRIES}..."
  HTTP_CODE=$(curl -sS -o /tmp/upload_response.json -w "%{http_code}" \
    -H "${AUTH_HEADER}" -H "Content-Type: application/gzip" \
    --data-binary "@${FILE_PATH}" "${UPLOAD_URL}?name=${ASSET_NAME}")

  if [[ "${HTTP_CODE}" =~ ^2 ]]; then
    echo "Upload succeeded (HTTP ${HTTP_CODE})"
    break
  fi

  echo "Upload failed (HTTP ${HTTP_CODE}), response:" >&2
  cat /tmp/upload_response.json >&2
  echo >&2

  if [[ "${attempt}" -eq "${MAX_RETRIES}" ]]; then
    echo "Error: upload failed after ${MAX_RETRIES} attempts." >&2
    exit 1
  fi

  ASSET_ID=$(curl -fsSL -H "${AUTH_HEADER}" "${API}/releases/${RELEASE_ID}/assets" | jq -r \
    ".[] | select(.name==\"${ASSET_NAME}\") | .id")
  if [[ -n "${ASSET_ID}" ]]; then
    curl -fsSL -X DELETE -H "${AUTH_HEADER}" "${API}/releases/assets/${ASSET_ID}" >/dev/null
    echo "Deleted partial asset before retry"
  fi

  sleep 30
done

UPLOADED_SIZE=$(curl -fsSL -H "${AUTH_HEADER}" "${API}/releases/${RELEASE_ID}/assets" | jq -r \
  ".[] | select(.name==\"${ASSET_NAME}\") | .size")
LOCAL_SIZE=$(stat -c%s "${FILE_PATH}" 2>/dev/null || stat -f%z "${FILE_PATH}")

if [[ -z "${UPLOADED_SIZE}" || "${UPLOADED_SIZE}" == "null" ]]; then
  echo "Error: asset not found in release after upload." >&2
  exit 1
fi

if [[ "${UPLOADED_SIZE}" -ne "${LOCAL_SIZE}" ]]; then
  echo "Error: size mismatch - local=${LOCAL_SIZE} uploaded=${UPLOADED_SIZE}" >&2
  exit 1
fi

echo "Verified: uploaded ${ASSET_NAME} (${UPLOADED_SIZE} bytes) to release ${DATE}"
