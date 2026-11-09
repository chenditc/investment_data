#!/usr/bin/env bash
set -euo pipefail
source /investment_data/upload_release.sh
preflight publish
[[ "$GITHUB_EVENT_NAME" == workflow_dispatch ]] || { die 'historical repair requires workflow_dispatch'; exit 1; }

tag=2026-11-08
target_id=438076075
old_archive_id=724447722
old_manifest_id=724454460
old_archive_digest=sha256:eef9e283af7fa0747462fb8c9a1304bc04b66d6a83d420ef6f2c06d605ce36cc
old_manifest_digest=sha256:3b9756b6610610238f26680b2e851aaaa4be4eeeefecc4a684fd7a8c17455484
expected_source=7bbtidfj86ieca3rje3illhu8u3ub3af
commit="$(select_normal_dolt_commit)"
[[ "$commit" == "$expected_source" ]] || { die 'historical repair source changed'; exit 1; }
WORK_DIR="$(mktemp -d /tmp/investment-data-issue163.XXXXXXXX)"
trap 'status=$?; rm -rf -- "$WORK_DIR"; exit "$status"' EXIT

build_pair "$tag" "$commit" /output
get_existing_release "$tag"
[[ "$RELEASE_ID" == "$target_id" ]]
[[ "$(list_assets | jq length)" == 2 ]]

assert_original() {
    local name=$1 id=$2 digest=$3 asset
    asset="$(slot_json "$(list_assets)" "$name")"
    jq -e --argjson id "$id" --arg digest "$digest" \
        '.id == $id and .digest == $digest and .state == "uploaded"' >/dev/null <<<"$asset"
}
assert_original "$ARCHIVE_NAME" "$old_archive_id" "$old_archive_digest"
assert_original "$MANIFEST_NAME" "$old_manifest_id" "$old_manifest_digest"
original_archive="$(redownload_named_asset "$ARCHIVE_NAME" "$WORK_DIR/original.tar.gz")"
original_manifest="$(redownload_named_asset "$MANIFEST_NAME" "$WORK_DIR/original.manifest.json")"
validate_pair "$WORK_DIR/original.tar.gz" "$WORK_DIR/original.manifest.json" "$tag"

backup_tag=backup-2026-11-08-before-issue163
status=0
backup="$(github_get_release_by_tag_optional "$backup_tag")" || status=$?
if ((status == 4)); then
    payload="$(jq -cn --arg tag "$backup_tag" '{tag_name:$tag,target_commitish:"a33e7e2173b1f67436387db4969babc9c122ede5",name:"Backup: 2026-11-08 before issue 163",body:"Original files retained before the issue 163 historical BSE factor repair. Use the normal dated release for data.",draft:false,prerelease:true,make_latest:"false"}')"
    backup="$(curl -fsSL -X POST -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H 'Content-Type: application/json' --data-binary "$payload" "${API_ROOT}/releases")"
elif ((status != 0)); then
    die 'unable to establish backup release'; exit 1
fi
jq -e --arg tag "$backup_tag" '.tag_name == $tag and .prerelease == true and .draft == false' >/dev/null <<<"$backup"
backup_id="$(jq -r .id <<<"$backup")"
backup_url="$(jq -r .html_url <<<"$backup")"
RELEASE_ID="$backup_id"
backup_archive="$(ensure_uploaded_asset "qlib_bin.original-${tag}-${old_archive_id}.tar.gz" "$WORK_DIR/original.tar.gz" application/gzip "$WORK_DIR/backup.tar.gz")"
backup_manifest="$(ensure_uploaded_asset "qlib_bin.original-${tag}-${old_manifest_id}.manifest.json" "$WORK_DIR/original.manifest.json" application/json "$WORK_DIR/backup.manifest.json")"
cmp -s "$WORK_DIR/original.tar.gz" "$WORK_DIR/backup.tar.gz"
cmp -s "$WORK_DIR/original.manifest.json" "$WORK_DIR/backup.manifest.json"
validate_pair "$WORK_DIR/backup.tar.gz" "$WORK_DIR/backup.manifest.json" "$tag"

RELEASE_ID="$target_id"
candidate_archive_name="qlib_bin.issue163-${GITHUB_SHA}.tar.gz"
candidate_manifest_name="qlib_bin.issue163-${GITHUB_SHA}.manifest.json"
candidate_archive="$(ensure_uploaded_asset "$candidate_archive_name" /output/qlib_bin.tar.gz application/gzip "$WORK_DIR/candidate.tar.gz")"
candidate_manifest="$(ensure_uploaded_asset "$candidate_manifest_name" /output/qlib_bin.manifest.json application/json "$WORK_DIR/candidate.manifest.json")"
validate_pair "$WORK_DIR/candidate.tar.gz" "$WORK_DIR/candidate.manifest.json" "$tag"

jq -n --arg phase prepared --arg tag "$tag" --arg source "$commit" \
    --arg run "https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}" \
    --arg backup_url "$backup_url" --argjson original_archive "$original_archive" \
    --argjson original_manifest "$original_manifest" --argjson backup_archive "$backup_archive" \
    --argjson backup_manifest "$backup_manifest" --argjson candidate_archive "$candidate_archive" \
    --argjson candidate_manifest "$candidate_manifest" \
    '{phase:$phase,tag:$tag,source_commit:$source,workflow_run:$run,backup_url:$backup_url,original_archive:$original_archive,original_manifest:$original_manifest,backup_archive:$backup_archive,backup_manifest:$backup_manifest,candidate_archive:$candidate_archive,candidate_manifest:$candidate_manifest}' >/output/repair-result.json
RELEASE_ID="$backup_id"
ensure_uploaded_asset repair-issue163-prepared.json /output/repair-result.json application/json "$WORK_DIR/prepared.json" >/dev/null
cmp -s /output/repair-result.json "$WORK_DIR/prepared.json"

RELEASE_ID="$target_id"
assets="$(list_assets)"
[[ "$(jq length <<<"$assets")" == 4 ]]
assert_original "$ARCHIVE_NAME" "$old_archive_id" "$old_archive_digest"
assert_original "$MANIFEST_NAME" "$old_manifest_id" "$old_manifest_digest"
[[ "$(slot_json "$assets" "$candidate_archive_name" | jq -r .id)" == "$(jq -r .id <<<"$candidate_archive")" ]]
[[ "$(slot_json "$assets" "$candidate_manifest_name" | jq -r .id)" == "$(jq -r .id <<<"$candidate_manifest")" ]]
github_delete_asset "$old_archive_id"
github_delete_asset "$old_manifest_id"
rename_asset() {
    local id=$1 name=$2 payload
    payload="$(jq -cn --arg name "$name" '{name:$name}')"
    curl -fsSL --retry 3 --retry-delay 2 -X PATCH \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" -H 'Content-Type: application/json' \
        --data-binary "$payload" "${API_ROOT}/releases/assets/${id}" >/dev/null
}
rename_asset "$(jq -r .id <<<"$candidate_archive")" "$ARCHIVE_NAME"
rename_asset "$(jq -r .id <<<"$candidate_manifest")" "$MANIFEST_NAME"
[[ "$(list_assets | jq length)" == 2 ]]
canonical_archive="$(redownload_named_asset "$ARCHIVE_NAME" "$WORK_DIR/accepted.tar.gz")"
canonical_manifest="$(redownload_named_asset "$MANIFEST_NAME" "$WORK_DIR/accepted.manifest.json")"
cmp -s "$WORK_DIR/accepted.tar.gz" /output/qlib_bin.tar.gz
cmp -s "$WORK_DIR/accepted.manifest.json" /output/qlib_bin.manifest.json
validate_pair "$WORK_DIR/accepted.tar.gz" "$WORK_DIR/accepted.manifest.json" "$tag"
jq --argjson archive "$canonical_archive" --argjson manifest "$canonical_manifest" \
    '.phase="accepted" | .canonical_archive=$archive | .canonical_manifest=$manifest' \
    /output/repair-result.json >"$WORK_DIR/accepted.json"
cp "$WORK_DIR/accepted.json" /output/repair-result.json
RELEASE_ID="$backup_id"
ensure_uploaded_asset repair-issue163-accepted.json /output/repair-result.json application/json "$WORK_DIR/accepted-receipt.json" >/dev/null
cmp -s /output/repair-result.json "$WORK_DIR/accepted-receipt.json"

body="$(printf 'Source-data repair for issue #163, applied by the verified main workflow.\n\nCorrected the remaining 217 BSE adjustment values on 2025-10-31 in both source tables. Raw OHLC, volume and amount are unchanged. Continuous index membership intervals are compacted while preserving absence and return intervals (#139).\n\nDolt source: `%s`\nCode: `%s`\nVerified workflow: https://github.com/%s/actions/runs/%s\nOriginal pair and repair receipts: %s\n\nHistorical SHT00018 coverage and previously documented stock VWAP anomalies remain unresolved.\n' "$commit" "$GITHUB_SHA" "$GITHUB_REPOSITORY" "$GITHUB_RUN_ID" "$backup_url")"
payload="$(jq -cn --arg body "$body" '{body:$body}')"
curl -fsSL --retry 3 -X PATCH -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -H 'Content-Type: application/json' --data-binary "$payload" "${API_ROOT}/releases/${target_id}" >/dev/null
printf 'Accepted repaired release %s with verified backup, source provenance and canonical downloads.\n' "$tag"
