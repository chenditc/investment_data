#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="chenditc/investment_data"
API_ROOT="https://api.github.com/repos/${REPOSITORY}"
UPLOAD_ROOT="https://uploads.github.com/repos/${REPOSITORY}"
PUBLISHER_LOCK="/tmp/investment-data-release-publisher.lock"
QLIB_COMMIT="b87a2c294d364a33fb739359886acffe8ec907d1"
REPOSITORY_REVISION_FILE="/opt/investment-data/REVISION"
QLIB_REVISION_FILE="/opt/investment-data/QLIB_REVISION"
FIXED_TAG="2026-07-20"
FIXED_DOLT_COMMIT="9vtplc2tar9ver7p6s1bus2oiedjvtqo"
FIXED_RELEASE_ID=356733573
FIXED_ORIGINAL_ASSET_ID=483488955
FIXED_ORIGINAL_SIZE=558140106
FIXED_ORIGINAL_DIGEST="sha256:1b1c073af75b69fecff85654cb020aa813c2d1761cab51048845140ae2cd2510"
FIXED_ORIGINAL_CREATED_AT="2026-07-20T13:26:30Z"
FIXED_ORIGINAL_UPDATED_AT="2026-07-20T13:26:48Z"
BACKUP_NAME="qlib_bin.original-2026-07-20-483488955.tar.gz"
RECEIPT_NAME="qlib-repair-2026-07-20.json"
ARCHIVE_NAME="qlib_bin.tar.gz"
MANIFEST_NAME="qlib_bin.manifest.json"
WORK_DIR=""
ORIGIN_AUTHORITY_VERIFIED=false

die() {
    printf 'Error: %s\n' "$*" >&2
    return 1
}

usage() {
    printf 'usage: %s [repair-2026-07-20]\n' "${0##*/}" >&2
}

sha256_file() {
    local digest
    digest="$(sha256sum "$1" | awk '{print $1}')" || return 1
    printf 'sha256:%s\n' "$digest"
}

file_size() {
    stat -c%s "$1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 \
        || { die "required command is unavailable: $1"; return 1; }
}

github_api_get() {
    curl -fsSL --retry 3 --retry-delay 2 \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Accept: application/vnd.github+json" \
        "${API_ROOT}$1"
}

github_get_release_by_tag_optional() {
    local tag=$1 response_file status
    response_file="$(mktemp "$WORK_DIR/api.XXXXXXXX")" || return 1
    status="$(curl -sSL -o "$response_file" -w '%{http_code}' \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Accept: application/vnd.github+json" \
        "${API_ROOT}/releases/tags/${tag}")" || return 1
    case "$status" in
        200) cat "$response_file" || return 1 ;;
        404) return 4 ;;
        *) die "GitHub release lookup failed with HTTP $status"; return 1 ;;
    esac
}

github_get_asset_by_id_optional() {
    local asset_id=$1 response_file status
    response_file="$(mktemp "$WORK_DIR/api.XXXXXXXX")" || return 1
    status="$(curl -sSL -o "$response_file" -w '%{http_code}' \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Accept: application/vnd.github+json" \
        "${API_ROOT}/releases/assets/${asset_id}")" || return 1
    case "$status" in
        200) cat "$response_file" || return 1 ;;
        404) return 4 ;;
        *) die "GitHub asset lookup failed with HTTP $status"; return 1 ;;
    esac
}

github_create_release() {
    local tag=$1 payload
    payload="$(jq -cn --arg tag "$tag" \
        '{tag_name:$tag,name:$tag,body:"Daily update release",draft:false,prerelease:false}')" \
        || return 1
    curl -fsSL --retry 3 --retry-delay 2 \
        -X POST \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Accept: application/vnd.github+json" \
        -H 'Content-Type: application/json' \
        --data-binary "$payload" \
        "${API_ROOT}/releases"
}

github_upload_asset() {
    local release_id=$1 name=$2 path=$3 content_type=$4
    curl -fsSL \
        -X POST \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Content-Type: ${content_type}" \
        --data-binary "@${path}" \
        "${UPLOAD_ROOT}/releases/${release_id}/assets?name=${name}"
}

github_download_asset() {
    local asset_id=$1 destination=$2
    curl -fsSL --retry 3 --retry-delay 2 \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        -H "Accept: application/octet-stream" \
        -o "$destination" \
        "${API_ROOT}/releases/assets/${asset_id}"
}

github_delete_asset() {
    local asset_id=$1
    curl -fsSL \
        -X DELETE \
        -H "Authorization: Bearer ${GITHUB_TOKEN}" \
        -H "X-GitHub-Api-Version: 2022-11-28" \
        "${API_ROOT}/releases/assets/${asset_id}" >/dev/null
}

list_assets() {
    github_api_get "/releases/${RELEASE_ID}/assets?per_page=100"
}

slot_json() {
    local assets=$1 name=$2 count
    count="$(jq --arg name "$name" '[.[] | select(.name == $name)] | length' <<<"$assets")" \
        || return 1
    ((count <= 1)) \
        || { die "duplicate release assets named $name"; return 1; }
    if ((count == 0)); then
        printf 'null\n'
    else
        jq -c --arg name "$name" '.[] | select(.name == $name)' <<<"$assets" \
            || return 1
    fi
}

slot_state() {
    local asset=$1 state size
    if [[ "$asset" == "null" ]]; then
        printf 'absent\n'
        return
    fi
    state="$(jq -r '.state' <<<"$asset")" || return 1
    size="$(jq -r '.size' <<<"$asset")" || return 1
    case "$state" in
        uploaded)
            [[ "$size" =~ ^[1-9][0-9]*$ ]] \
                || { die "uploaded asset has invalid size"; return 1; }
            printf 'uploaded\n'
            ;;
        starter)
            [[ "$size" == "0" ]] \
                || { die "starter asset is not empty"; return 1; }
            printf 'starter\n'
            ;;
        *) die "asset has invalid state: $state"; return 1 ;;
    esac
}

verify_file_against_asset() {
    local asset=$1 path=$2 state expected_size actual_size expected_digest actual_digest
    state="$(jq -r '.state' <<<"$asset")" || return 1
    expected_size="$(jq -r '.size' <<<"$asset")" || return 1
    actual_size="$(file_size "$path")" || return 1
    expected_digest="$(jq -r '.digest' <<<"$asset")" || return 1
    actual_digest="$(sha256_file "$path")" || return 1
    [[ "$state" == "uploaded" ]] \
        || { die "asset is not uploaded"; return 1; }
    [[ "$expected_size" == "$actual_size" ]] \
        || { die "asset size does not match downloaded bytes"; return 1; }
    [[ "$expected_digest" == "$actual_digest" ]] \
        || { die "asset digest does not match downloaded bytes"; return 1; }
}

redownload_named_asset() {
    local name=$1 destination=$2 assets asset asset_id
    local state
    assets="$(list_assets)" || return 1
    asset="$(slot_json "$assets" "$name")" || return 1
    state="$(slot_state "$asset")" || return 1
    [[ "$state" == "uploaded" ]] \
        || { die "required uploaded asset is missing: $name"; return 1; }
    asset_id="$(jq -r '.id' <<<"$asset")" || return 1
    github_download_asset "$asset_id" "$destination" || return 1
    verify_file_against_asset "$asset" "$destination" || return 1
    printf '%s\n' "$asset"
}

recover_exact_starter() {
    local name=$1 expected_id=$2 expected_release_id=$3 assets asset state probe after
    [[ "$ORIGIN_AUTHORITY_VERIFIED" == true ]] \
        || { die "starter recovery lacks originating authority"; return 1; }
    [[ "$RELEASE_ID" == "$expected_release_id" ]] \
        || { die "starter recovery release identity changed"; return 1; }
    assets="$(list_assets)" || return 1
    asset="$(slot_json "$assets" "$name")" || return 1
    state="$(slot_state "$asset")" || return 1
    [[ "$state" == "starter" ]] \
        || { die "starter recovery target is not an empty starter"; return 1; }
    [[ "$(jq -r '.id' <<<"$asset")" == "$expected_id" ]] \
        || { die "starter identity changed"; return 1; }

    github_delete_asset "$expected_id" || true
    if probe="$(github_get_asset_by_id_optional "$expected_id")"; then
        die "starter asset still exists after deletion"
        return 1
    elif (($? != 4)); then
        die "unable to confirm starter deletion by ID"
        return 1
    fi
    after="$(list_assets)" || return 1
    [[ "$(jq --arg name "$name" --argjson id "$expected_id" \
        '[.[] | select(.name == $name or .id == $id)] | length' <<<"$after")" == "0" ]] \
        || { die "starter deletion was not confirmed by release listing"; return 1; }
}

ensure_uploaded_asset() {
    local name=$1 source=$2 content_type=$3 destination=$4
    local transition assets asset state asset_id upload_attempts=0 upload_status
    local failed_assets failed_asset failed_state attempted_release_id
    for transition in 1 2 3 4 5 6 7 8; do
        assets="$(list_assets)" || return 1
        asset="$(slot_json "$assets" "$name")" || return 1
        state="$(slot_state "$asset")" || return 1
        case "$state" in
            absent)
                ((upload_attempts += 1))
                ((upload_attempts <= 3)) \
                    || { die "upload retry limit reached: $name"; return 1; }
                attempted_release_id="$RELEASE_ID"
                upload_status=0
                github_upload_asset "$RELEASE_ID" "$name" "$source" "$content_type" \
                    >/dev/null || upload_status=$?
                if ((upload_status != 0)); then
                    # Only an empty starter observed immediately after this
                    # invocation's ambiguous upload belongs to this step.
                    failed_assets="$(list_assets)" || return 1
                    failed_asset="$(slot_json "$failed_assets" "$name")" || return 1
                    failed_state="$(slot_state "$failed_asset")" || return 1
                    if [[ "$failed_state" == "starter" ]]; then
                        asset_id="$(jq -r '.id' <<<"$failed_asset")" || return 1
                        recover_exact_starter "$name" "$asset_id" "$attempted_release_id" \
                            || return 1
                    fi
                fi
                ;;
            starter)
                die "starter asset was not created by this invocation's current upload attempt: $name"
                return 1
                ;;
            uploaded)
                asset_id="$(jq -r '.id' <<<"$asset")" || return 1
                github_download_asset "$asset_id" "$destination" || return 1
                verify_file_against_asset "$asset" "$destination" || return 1
                cmp -s "$source" "$destination" \
                    || { die "uploaded asset bytes conflict: $name"; return 1; }
                printf '%s\n' "$asset"
                return 0
                ;;
        esac
    done
    die "unable to establish exact uploaded asset: $name"
}

validate_pair() {
    python3 /investment_data/qlib/validate_archive.py \
        --archive "$1" --manifest "$2" --expected-tag "$3" --require-publishable >/dev/null
}

select_normal_dolt_commit() {
    local dolt_dir=/dolt checkout=/dolt/investment_data commit status_output
    mkdir -p "$dolt_dir" \
        || { die "cannot create shared Dolt directory"; return 1; }
    exec 8>"${dolt_dir}/.investment-data.lock" \
        || { die "cannot open shared Dolt checkout lock"; return 1; }
    flock -n 8 \
        || { die "shared Dolt checkout lock is held"; return 1; }
    if [[ ! -d "$checkout/.dolt" ]]; then
        printf 'Cloning shared Dolt checkout.\n' >&2
        (cd "$dolt_dir" && dolt clone chenditc/investment_data) >&2 \
            || { die "failed to clone shared Dolt checkout"; return 1; }
    fi
    status_output="$(cd "$checkout" && dolt status)" \
        || { die "failed to inspect shared Dolt checkout"; return 1; }
    grep -q 'nothing to commit, working tree clean' <<<"$status_output" \
        || { die "shared Dolt checkout is not clean"; return 1; }
    printf 'Fetching shared Dolt origin/master.\n' >&2
    (cd "$checkout" && dolt fetch --silent origin master) >&2 \
        || { die "failed to fetch shared Dolt origin/master"; return 1; }
    commit="$(cd "$checkout" \
        && dolt sql -r csv -q "SELECT DOLT_HASHOF('origin/master') AS value" \
        | tail -n 1 | tr -d '\r')" \
        || { die "failed to query the fetched Dolt commit"; return 1; }
    flock -u 8 \
        || { die "failed to release shared Dolt checkout lock"; return 1; }
    exec 8>&- \
        || { die "failed to close shared Dolt checkout lock"; return 1; }
    [[ "$commit" =~ ^[0-9a-v]{32}$ ]] \
        || { die "unable to select Dolt commit"; return 1; }
    printf '%s\n' "$commit"
}

build_pair() {
    local tag=$1 dolt_commit=$2 output_dir=$3
    env -u TRACE -u DUMP_QLIB_MAX_WORKERS -u OUTPUT_DIR \
        OUTPUT_DIR="$output_dir" \
        BUILD_RELEASE_TAG="$tag" \
        BUILD_INVESTMENT_DATA_COMMIT="$GITHUB_SHA" \
        BUILD_QLIB_COMMIT="$QLIB_COMMIT" \
        BUILD_IMAGE_DIGEST="$PUBLICATION_IMAGE_DIGEST" \
        BUILD_DOLT_COMMIT="$dolt_commit" \
        bash /investment_data/dump_qlib_bin.sh || return 1
    validate_pair "$output_dir/$ARCHIVE_NAME" "$output_dir/$MANIFEST_NAME" "$tag" \
        || return 1
}

preflight() {
    local operation=$1 variable baked_repository baked_qlib
    for variable in QLIB_BUILD_ID QLIB_BUILD_ROOT CLEAN_QLIB_BUILD_ROOT CHECK_FRESHNESS REPO DATE UPLOAD_RELEASE_LOCK_FILE; do
        if [[ -v "$variable" ]]; then
            die "$variable is not a supported publisher input"
            return 1
        fi
    done
    [[ "${GITHUB_ACTIONS:-}" == "true" ]] \
        || { die "publisher requires GitHub Actions authority"; return 1; }
    [[ "${GITHUB_REPOSITORY:-}" == "$REPOSITORY" ]] \
        || { die "unexpected GitHub repository"; return 1; }
    [[ "${GITHUB_REF:-}" == "refs/heads/main" ]] \
        || { die "publisher requires main branch authority"; return 1; }
    [[ "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]] \
        || { die "invalid GitHub commit authority"; return 1; }
    [[ "${GITHUB_RUN_ID:-}" =~ ^[1-9][0-9]*$ ]] \
        || { die "invalid GitHub run ID"; return 1; }
    [[ "${GITHUB_RUN_ATTEMPT:-}" =~ ^[1-9][0-9]*$ ]] \
        || { die "invalid GitHub run attempt"; return 1; }
    [[ "${PUBLICATION_IMAGE_DIGEST:-}" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || { die "invalid image digest authority"; return 1; }
    [[ "${PUBLICATION_BAKED_REPOSITORY_REVISION:-}" == "$GITHUB_SHA" ]] \
        || { die "launcher repository revision mismatch"; return 1; }
    [[ "${PUBLICATION_BAKED_QLIB_REVISION:-}" == "$QLIB_COMMIT" ]] \
        || { die "launcher qlib revision mismatch"; return 1; }
    if [[ "$operation" == "repair-2026-07-20" ]]; then
        [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]] \
            || { die "repair requires workflow_dispatch"; return 1; }
    else
        [[ "${GITHUB_EVENT_NAME:-}" == "schedule" || "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]] \
            || { die "publish requires schedule or workflow_dispatch"; return 1; }
    fi

    [[ -n "${GITHUB_TOKEN:-}" ]] || { die "GITHUB_TOKEN is required"; return 1; }
    for variable in curl jq python3 sha256sum stat flock dolt git cmp mktemp; do
        require_command "$variable" || return 1
    done
    [[ -r "$REPOSITORY_REVISION_FILE" && -r "$QLIB_REVISION_FILE" ]] \
        || { die "baked revision files are unavailable"; return 1; }
    baked_repository="$(<"$REPOSITORY_REVISION_FILE")" || return 1
    baked_qlib="$(<"$QLIB_REVISION_FILE")" || return 1
    [[ "$baked_repository" == "$GITHUB_SHA" \
        && "$baked_repository" == "$PUBLICATION_BAKED_REPOSITORY_REVISION" ]] \
        || { die "baked repository revision mismatch"; return 1; }
    [[ "$baked_qlib" == "$QLIB_COMMIT" \
        && "$baked_qlib" == "$PUBLICATION_BAKED_QLIB_REVISION" ]] \
        || { die "baked qlib revision mismatch"; return 1; }

    exec 9>"$PUBLISHER_LOCK" \
        || { die "cannot open publisher lock"; return 1; }
    flock -n 9 || { die "publisher lock is held by another process"; return 1; }
    ORIGIN_AUTHORITY_VERIFIED=true
}

get_or_create_release() {
    local tag=$1 release
    if release="$(github_get_release_by_tag_optional "$tag")"; then
        :
    elif (($? == 4)); then
        release="$(github_create_release "$tag")" || return 1
    else
        die "unable to resolve release $tag"
        return 1
    fi
    jq -e --arg tag "$tag" '
        (.id | type) == "number"
        and .id > 0
        and .tag_name == $tag
        and .draft == false
        and .prerelease == false
    ' >/dev/null 2>&1 <<<"$release" \
        || { die "release identity, draft, or prerelease state is invalid"; return 1; }
    RELEASE_ID="$(jq -r '.id' <<<"$release")" || return 1
}

publish_current() {
    local tag dolt_commit build_dir archive manifest assets archive_slot manifest_slot
    local archive_state manifest_state downloaded_archive downloaded_manifest
    tag="$(TZ=Asia/Shanghai date +%F)" || return 1
    dolt_commit="$(select_normal_dolt_commit)" || return 1
    build_dir="$WORK_DIR/build"
    mkdir -p "$build_dir" || return 1
    build_pair "$tag" "$dolt_commit" "$build_dir" || return 1
    archive="$build_dir/$ARCHIVE_NAME"
    manifest="$build_dir/$MANIFEST_NAME"

    get_or_create_release "$tag" || return 1
    assets="$(list_assets)" || return 1
    archive_slot="$(slot_json "$assets" "$ARCHIVE_NAME")" || return 1
    manifest_slot="$(slot_json "$assets" "$MANIFEST_NAME")" || return 1
    archive_state="$(slot_state "$archive_slot")" || return 1
    manifest_state="$(slot_state "$manifest_slot")" || return 1
    if [[ "$archive_state" == "absent" ]]; then
        [[ "$manifest_state" == "absent" ]] \
            || { die "manifest exists before canonical archive"; return 1; }
    elif [[ "$archive_state" == "uploaded" ]]; then
        [[ "$manifest_state" == "absent" || "$manifest_state" == "uploaded" ]] \
            || { die "invalid canonical manifest state"; return 1; }
    else
        die "pre-existing canonical starter conflicts with current-attempt ownership"
        return 1
    fi

    downloaded_archive="$WORK_DIR/published-archive"
    downloaded_manifest="$WORK_DIR/published-manifest"
    ensure_uploaded_asset "$ARCHIVE_NAME" "$archive" application/gzip "$downloaded_archive" \
        >/dev/null || return 1
    ensure_uploaded_asset "$MANIFEST_NAME" "$manifest" application/json "$downloaded_manifest" \
        >/dev/null || return 1
    redownload_named_asset "$ARCHIVE_NAME" "$downloaded_archive" >/dev/null || return 1
    redownload_named_asset "$MANIFEST_NAME" "$downloaded_manifest" >/dev/null || return 1
    cmp -s "$archive" "$downloaded_archive" \
        || { die "canonical archive differs from local build"; return 1; }
    cmp -s "$manifest" "$downloaded_manifest" \
        || { die "canonical manifest differs from local build"; return 1; }
    validate_pair "$downloaded_archive" "$downloaded_manifest" "$tag" || return 1
    printf 'Validated release %s with immutable archive and manifest assets.\n' "$tag"
}

require_original_identity() {
    local asset=$1
    [[ "$(jq -r '.id' <<<"$asset")" == "$FIXED_ORIGINAL_ASSET_ID" \
        && "$(jq -r '.name' <<<"$asset")" == "$ARCHIVE_NAME" \
        && "$(jq -r '.state' <<<"$asset")" == "uploaded" \
        && "$(jq -r '.size' <<<"$asset")" == "$FIXED_ORIGINAL_SIZE" \
        && "$(jq -r '.digest' <<<"$asset")" == "$FIXED_ORIGINAL_DIGEST" \
        && "$(jq -r '.created_at' <<<"$asset")" == "$FIXED_ORIGINAL_CREATED_AT" \
        && "$(jq -r '.updated_at' <<<"$asset")" == "$FIXED_ORIGINAL_UPDATED_AT" ]] \
        || { die "original 2026-07-20 asset identity changed"; return 1; }
}

create_receipt() {
    local manifest_path=$1 output_path=$2 backup_json=$3 candidate_archive_json=$4 candidate_manifest_json=$5
    local canonical_archive_size canonical_archive_digest canonical_manifest_size canonical_manifest_digest
    canonical_archive_size="$(file_size "$CANDIDATE_ARCHIVE_LOCAL")" || return 1
    canonical_archive_digest="$(sha256_file "$CANDIDATE_ARCHIVE_LOCAL")" || return 1
    canonical_manifest_size="$(file_size "$CANDIDATE_MANIFEST_LOCAL")" || return 1
    canonical_manifest_digest="$(sha256_file "$CANDIDATE_MANIFEST_LOCAL")" || return 1
    python3 - "$manifest_path" "$output_path" "$backup_json" "$candidate_archive_json" \
        "$candidate_manifest_json" "$canonical_archive_size" "$canonical_archive_digest" \
        "$canonical_manifest_size" "$canonical_manifest_digest" \
        "$FIXED_ORIGINAL_SIZE" "$FIXED_ORIGINAL_DIGEST" \
        "$FIXED_ORIGINAL_CREATED_AT" "$FIXED_ORIGINAL_UPDATED_AT" <<'PY'
import json
import sys

manifest_path, output_path = sys.argv[1:3]
backup, candidate_archive, candidate_manifest = map(json.loads, sys.argv[3:6])

def record(asset):
    return {
        "asset_id": asset["id"],
        "name": asset["name"],
        "state": asset["state"],
        "size_bytes": asset["size"],
        "sha256": asset["digest"],
        "created_at": asset["created_at"],
        "updated_at": asset["updated_at"],
    }

with open(manifest_path, encoding="utf-8") as stream:
    manifest = json.load(stream)

receipt = {
    "operation": "repair-2026-07-20",
    "release_tag": "2026-07-20",
    "release_id": 356733573,
    "authority": {
        "investment_data_commit": manifest["investment_data_commit"],
        "qlib_commit": manifest["qlib_commit"],
        "dolt_commit": manifest["dolt_commit"],
        "image_digest": manifest["image_digest"],
    },
    "manifest": manifest,
    "assets": {
        "original": {
            "asset_id": 483488955,
            "name": "qlib_bin.tar.gz",
            "state": "uploaded",
            "size_bytes": int(sys.argv[10]),
            "sha256": sys.argv[11],
            "created_at": sys.argv[12],
            "updated_at": sys.argv[13],
        },
        "backup": record(backup),
        "candidate_archive": record(candidate_archive),
        "candidate_manifest": record(candidate_manifest),
        "canonical_archive": {
            "asset_id": None,
            "name": "qlib_bin.tar.gz",
            "state": "uploaded",
            "size_bytes": int(sys.argv[6]),
            "sha256": sys.argv[7],
            "created_at": None,
            "updated_at": None,
        },
        "canonical_manifest": {
            "asset_id": None,
            "name": "qlib_bin.manifest.json",
            "state": "uploaded",
            "size_bytes": int(sys.argv[8]),
            "sha256": sys.argv[9],
            "created_at": None,
            "updated_at": None,
        },
    },
}
with open(output_path, "w", encoding="utf-8", newline="\n") as stream:
    json.dump(receipt, stream, separators=(",", ":"))
    stream.write("\n")
PY
    return $?
}

validate_repair_receipt() {
    local phase=$1 receipt_path=$2 manifest_path=$3 original_json=$4 backup_json=$5
    local candidate_archive_json=$6 candidate_manifest_json=$7 canonical_archive_json=$8
    local canonical_manifest_json=$9
    shift 9
    local backup_path=$1 candidate_archive_path=$2 candidate_manifest_path=$3
    local canonical_archive_path=$4 canonical_manifest_path=$5
    python3 - "$phase" "$receipt_path" "$manifest_path" \
        "$original_json" "$backup_json" "$candidate_archive_json" \
        "$candidate_manifest_json" "$canonical_archive_json" "$canonical_manifest_json" \
        "$backup_path" "$candidate_archive_path" "$candidate_manifest_path" \
        "$canonical_archive_path" "$canonical_manifest_path" \
        "$CANDIDATE_ARCHIVE_NAME" "$CANDIDATE_MANIFEST_NAME" \
        "$FIXED_ORIGINAL_SIZE" "$FIXED_ORIGINAL_DIGEST" \
        "$FIXED_ORIGINAL_CREATED_AT" "$FIXED_ORIGINAL_UPDATED_AT" <<'PY'
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

(
    phase,
    receipt_path,
    manifest_path,
    original_json,
    backup_json,
    candidate_archive_json,
    candidate_manifest_json,
    canonical_archive_json,
    canonical_manifest_json,
    backup_path,
    candidate_archive_path,
    candidate_manifest_path,
    canonical_archive_path,
    canonical_manifest_path,
    candidate_archive_name,
    candidate_manifest_name,
    fixed_original_size,
    fixed_original_digest,
    fixed_original_created_at,
    fixed_original_updated_at,
) = sys.argv[1:]

TOP_KEYS = ["operation", "release_tag", "release_id", "authority", "manifest", "assets"]
AUTHORITY_KEYS = ["investment_data_commit", "qlib_commit", "dolt_commit", "image_digest"]
MANIFEST_KEYS = [
    "release_tag",
    "target_trade_date",
    "future_start_date",
    "future_end_date",
    "dolt_commit",
    "investment_data_commit",
    "qlib_commit",
    "image_digest",
    "archive_size_bytes",
    "archive_sha256",
]
ASSET_KEYS = [
    "original",
    "backup",
    "candidate_archive",
    "candidate_manifest",
    "canonical_archive",
    "canonical_manifest",
]
RECORD_KEYS = [
    "asset_id",
    "name",
    "state",
    "size_bytes",
    "sha256",
    "created_at",
    "updated_at",
]
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
GIT_RE = re.compile(r"[0-9a-f]{40}\Z")
DOLT_RE = re.compile(r"[0-9a-v]{32}\Z")
RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z"
)


def reject(message):
    raise ValueError(message)


def no_duplicates(pairs):
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        reject("duplicate JSON key")
    return dict(pairs)


def load_exact(path):
    try:
        return json.loads(Path(path).read_bytes().decode("utf-8"), object_pairs_hook=no_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid receipt or manifest JSON") from exc


def positive_integer(value):
    return type(value) is int and value > 0


def canonical_digest(value):
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def rfc3339(value):
    if not isinstance(value, str) or RFC3339_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def file_identity(path):
    data = Path(path).read_bytes()
    return len(data), "sha256:" + hashlib.sha256(data).hexdigest()


def load_live(raw, label):
    if raw == "null":
        return None
    try:
        asset = json.loads(raw, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} live asset JSON is invalid") from exc
    required = ("id", "name", "state", "size", "digest", "created_at", "updated_at")
    if not isinstance(asset, dict) or any(key not in asset for key in required):
        reject(f"{label} live asset fields are incomplete")
    if not positive_integer(asset["id"]):
        reject(f"{label} live asset ID is invalid")
    if not isinstance(asset["name"], str) or asset["state"] != "uploaded":
        reject(f"{label} live asset name/state is invalid")
    if not positive_integer(asset["size"]) or not canonical_digest(asset["digest"]):
        reject(f"{label} live asset size/digest is invalid")
    if not rfc3339(asset["created_at"]) or not rfc3339(asset["updated_at"]):
        reject(f"{label} live asset timestamp is invalid")
    return asset


receipt = load_exact(receipt_path)
manifest = load_exact(manifest_path)
if not isinstance(receipt, dict) or list(receipt) != TOP_KEYS:
    reject("receipt top-level schema is invalid")
if (
    not isinstance(manifest, dict)
    or list(manifest) != MANIFEST_KEYS
    or not isinstance(receipt["manifest"], dict)
    or list(receipt["manifest"]) != MANIFEST_KEYS
    or receipt["manifest"] != manifest
):
    reject("receipt manifest is not the exact downloaded manifest")
if any(
    not isinstance(manifest[key], str) or DATE_RE.fullmatch(manifest[key]) is None
    for key in MANIFEST_KEYS[:4]
):
    reject("receipt manifest date form is invalid")
try:
    if any(
        datetime.date.fromisoformat(manifest[key]).isoformat() != manifest[key]
        for key in MANIFEST_KEYS[:4]
    ):
        reject("receipt manifest date value is invalid")
except ValueError as exc:
    raise ValueError("receipt manifest date value is invalid") from exc
if (
    DOLT_RE.fullmatch(manifest["dolt_commit"]) is None
    or GIT_RE.fullmatch(manifest["investment_data_commit"]) is None
    or manifest["qlib_commit"] != "b87a2c294d364a33fb739359886acffe8ec907d1"
    or not canonical_digest(manifest["image_digest"])
    or not positive_integer(manifest["archive_size_bytes"])
    or not canonical_digest(manifest["archive_sha256"])
):
    reject("receipt manifest value/type is invalid")
if receipt["operation"] != "repair-2026-07-20" or receipt["release_tag"] != "2026-07-20":
    reject("receipt operation/tag is invalid")
if type(receipt["release_id"]) is not int or receipt["release_id"] != 356733573:
    reject("receipt release ID is invalid")

authority = receipt["authority"]
if not isinstance(authority, dict) or list(authority) != AUTHORITY_KEYS:
    reject("receipt authority schema is invalid")
if authority != {
    "investment_data_commit": manifest["investment_data_commit"],
    "qlib_commit": manifest["qlib_commit"],
    "dolt_commit": manifest["dolt_commit"],
    "image_digest": manifest["image_digest"],
}:
    reject("receipt authority differs from manifest")

assets = receipt["assets"]
if not isinstance(assets, dict) or list(assets) != ASSET_KEYS:
    reject("receipt must contain the authoritative six asset records")
expected_names = {
    "original": "qlib_bin.tar.gz",
    "backup": "qlib_bin.original-2026-07-20-483488955.tar.gz",
    "candidate_archive": candidate_archive_name,
    "candidate_manifest": candidate_manifest_name,
    "canonical_archive": "qlib_bin.tar.gz",
    "canonical_manifest": "qlib_bin.manifest.json",
}
for label, record in assets.items():
    if not isinstance(record, dict) or list(record) != RECORD_KEYS:
        reject(f"{label} receipt record schema is invalid")
    nullable = label in ("canonical_archive", "canonical_manifest")
    if nullable:
        if record["asset_id"] is not None or record["created_at"] is not None or record["updated_at"] is not None:
            reject(f"{label} planned identity fields must be null")
    else:
        if not positive_integer(record["asset_id"]):
            reject(f"{label} receipt asset ID is invalid")
        if not rfc3339(record["created_at"]) or not rfc3339(record["updated_at"]):
            reject(f"{label} receipt timestamp is invalid")
    if record["name"] != expected_names[label] or record["state"] != "uploaded":
        reject(f"{label} receipt name/state is invalid")
    if not positive_integer(record["size_bytes"]) or not canonical_digest(record["sha256"]):
        reject(f"{label} receipt size/digest is invalid")

original = assets["original"]
if original != {
    "asset_id": 483488955,
    "name": "qlib_bin.tar.gz",
    "state": "uploaded",
    "size_bytes": int(fixed_original_size),
    "sha256": fixed_original_digest,
    "created_at": fixed_original_created_at,
    "updated_at": fixed_original_updated_at,
}:
    reject("receipt original record differs from captured authority")

if assets["backup"]["size_bytes"] != original["size_bytes"] or assets["backup"]["sha256"] != original["sha256"]:
    reject("receipt backup does not preserve the original identity")
if (
    assets["candidate_archive"]["size_bytes"] != manifest["archive_size_bytes"]
    or assets["candidate_archive"]["sha256"] != manifest["archive_sha256"]
):
    reject("receipt candidate archive differs from manifest")
if any(
    assets[canonical][field] != assets[candidate][field]
    for canonical, candidate in (
        ("canonical_archive", "candidate_archive"),
        ("canonical_manifest", "candidate_manifest"),
    )
    for field in ("size_bytes", "sha256")
):
    reject("receipt planned canonical pair differs from candidate pair")

for label, path in (
    ("backup", backup_path),
    ("candidate_archive", candidate_archive_path),
    ("candidate_manifest", candidate_manifest_path),
    ("canonical_archive", canonical_archive_path),
    ("canonical_manifest", canonical_manifest_path),
):
    if path != "-" and file_identity(path) != (
        assets[label]["size_bytes"],
        assets[label]["sha256"],
    ):
        reject(f"{label} downloaded bytes differ from receipt")

live_assets = {
    "original": load_live(original_json, "original"),
    "backup": load_live(backup_json, "backup"),
    "candidate_archive": load_live(candidate_archive_json, "candidate_archive"),
    "candidate_manifest": load_live(candidate_manifest_json, "candidate_manifest"),
    "canonical_archive": load_live(canonical_archive_json, "canonical_archive"),
    "canonical_manifest": load_live(canonical_manifest_json, "canonical_manifest"),
}
if phase == "pre-delete":
    if live_assets["original"] is None or live_assets["canonical_archive"] is not None or live_assets["canonical_manifest"] is not None:
        reject("pre-delete live asset set is invalid")
elif phase == "post-delete":
    if live_assets["original"] is not None:
        reject("post-delete original is unexpectedly present")
elif phase == "accepted":
    if live_assets["original"] is not None or live_assets["canonical_archive"] is None or live_assets["canonical_manifest"] is None:
        reject("accepted live asset set is invalid")
else:
    reject("unknown fixed repair receipt phase")

for required in ("backup", "candidate_archive", "candidate_manifest"):
    if live_assets[required] is None:
        reject(f"required live asset is absent: {required}")
for label, live in live_assets.items():
    if live is None:
        continue
    mapped = {
        "asset_id": live["id"],
        "name": live["name"],
        "state": live["state"],
        "size_bytes": live["size"],
        "sha256": live["digest"],
        "created_at": live["created_at"],
        "updated_at": live["updated_at"],
    }
    for field, expected in assets[label].items():
        if expected is not None and mapped[field] != expected:
            reject(f"{label} live field differs from receipt: {field}")
PY
}

validate_repair_prefix() {
    local assets=$1 original_present=$2 candidate_archive_state candidate_manifest_state backup_state receipt_state
    local canonical_archive_state canonical_manifest_state unexpected
    unexpected="$(jq --arg archive "$CANDIDATE_ARCHIVE_NAME" --arg manifest "$CANDIDATE_MANIFEST_NAME" \
        '[.[] | select((.name | startswith("qlib_bin.repair-2026-07-20-")) and .name != $archive and .name != $manifest)] | length' <<<"$assets")" \
        || return 1
    [[ "$unexpected" == "0" ]] \
        || { die "candidate authority does not match this workflow commit and image"; return 1; }

    candidate_archive_state="$(slot_state "$(slot_json "$assets" "$CANDIDATE_ARCHIVE_NAME")")" \
        || return 1
    candidate_manifest_state="$(slot_state "$(slot_json "$assets" "$CANDIDATE_MANIFEST_NAME")")" \
        || return 1
    backup_state="$(slot_state "$(slot_json "$assets" "$BACKUP_NAME")")" || return 1
    receipt_state="$(slot_state "$(slot_json "$assets" "$RECEIPT_NAME")")" || return 1
    canonical_manifest_state="$(slot_state "$(slot_json "$assets" "$MANIFEST_NAME")")" \
        || return 1

    if [[ "$original_present" == true ]]; then
        [[ "$canonical_manifest_state" == "absent" ]] \
            || { die "canonical manifest exists before original deletion"; return 1; }
        if [[ "$candidate_archive_state" == "absent" ]]; then
            [[ "$candidate_manifest_state/$backup_state/$receipt_state" == "absent/absent/absent" ]] \
                || { die "repair assets are not a valid preparation prefix"; return 1; }
        elif [[ "$candidate_archive_state" != "uploaded" ]]; then
            die "pre-existing candidate archive starter conflicts with current-attempt ownership"
            return 1
        elif [[ "$candidate_manifest_state" == "absent" ]]; then
            [[ "$backup_state/$receipt_state" == "absent/absent" ]] \
                || { die "repair assets are not a valid preparation prefix"; return 1; }
        elif [[ "$candidate_manifest_state" != "uploaded" ]]; then
            die "pre-existing candidate manifest starter conflicts with current-attempt ownership"
            return 1
        elif [[ "$backup_state" == "absent" ]]; then
            [[ "$receipt_state" == "absent" ]] \
                || { die "receipt exists before verified backup"; return 1; }
        elif [[ "$backup_state" != "uploaded" ]]; then
            die "pre-existing backup starter conflicts with current-attempt ownership"
            return 1
        elif [[ "$receipt_state" != "absent" && "$receipt_state" != "uploaded" ]]; then
            die "pre-existing receipt starter conflicts with current-attempt ownership"
            return 1
        fi
    else
        [[ "$candidate_archive_state/$candidate_manifest_state/$backup_state/$receipt_state" == \
            "uploaded/uploaded/uploaded/uploaded" ]] \
            || { die "original is absent without complete prepared evidence"; return 1; }
        canonical_archive_state="$(slot_state "$(slot_json "$assets" "$ARCHIVE_NAME")")" \
            || return 1
        if [[ "$canonical_archive_state" == "absent" ]]; then
            [[ "$canonical_manifest_state" == "absent" ]] \
                || { die "canonical manifest exists before canonical archive"; return 1; }
        elif [[ "$canonical_archive_state" != "uploaded" ]]; then
            die "pre-existing canonical archive starter conflicts with current-attempt ownership"
            return 1
        elif [[ "$canonical_manifest_state" != "absent" && "$canonical_manifest_state" != "uploaded" ]]; then
            die "pre-existing canonical manifest starter conflicts with current-attempt ownership"
            return 1
        fi
    fi
}

verify_fixed_release() {
    local release
    release="$(github_api_get "/releases/${FIXED_RELEASE_ID}")" || return 1
    jq -e --arg tag "$FIXED_TAG" --argjson id "$FIXED_RELEASE_ID" '
        .id == $id
        and .tag_name == $tag
        and .draft == false
        and .prerelease == false
    ' >/dev/null 2>&1 <<<"$release" \
        || { die "fixed release identity, draft, or prerelease state is invalid"; return 1; }
    RELEASE_ID=$FIXED_RELEASE_ID
}

delete_original_after_gate() {
    local probe assets
    github_delete_asset "$FIXED_ORIGINAL_ASSET_ID" || true
    if probe="$(github_get_asset_by_id_optional "$FIXED_ORIGINAL_ASSET_ID")"; then
        die "original asset still exists after deletion"
        return 1
    elif (($? != 4)); then
        die "unable to confirm original deletion by ID"
        return 1
    fi
    assets="$(list_assets)" || return 1
    [[ "$(jq --argjson id "$FIXED_ORIGINAL_ASSET_ID" --arg name "$ARCHIVE_NAME" \
        '[.[] | select(.id == $id or .name == $name)] | length' <<<"$assets")" == "0" ]] \
        || { die "original deletion was not confirmed by release listing"; return 1; }
}

repair_fixed_release() {
    local build_dir archive manifest archive_digest image_hex candidate_stem
    local assets original_asset original_by_id original_present original_local backup_local
    local candidate_archive_json candidate_manifest_json backup_json receipt_json
    local candidate_archive_download candidate_manifest_download receipt_local receipt_download
    local canonical_archive_json canonical_manifest_json
    local canonical_archive_download canonical_manifest_download receipt_phase

    build_dir="$WORK_DIR/build"
    mkdir -p "$build_dir" || return 1
    build_pair "$FIXED_TAG" "$FIXED_DOLT_COMMIT" "$build_dir" || return 1
    archive="$build_dir/$ARCHIVE_NAME"
    manifest="$build_dir/$MANIFEST_NAME"
    [[ "$(jq -r '.target_trade_date' "$manifest")" == "2026-07-20" \
        && "$(jq -r '.future_start_date' "$manifest")" == "2026-07-21" \
        && "$(jq -r '.future_end_date' "$manifest")" == "2026-12-31" ]] \
        || { die "fixed repair manifest dates changed"; return 1; }
    archive_digest="$(sha256_file "$archive")" || return 1
    image_hex="${PUBLICATION_IMAGE_DIGEST#sha256:}"
    candidate_stem="qlib_bin.repair-2026-07-20-${GITHUB_SHA}-${image_hex}-${archive_digest#sha256:}"
    CANDIDATE_ARCHIVE_NAME="${candidate_stem}.tar.gz"
    CANDIDATE_MANIFEST_NAME="${candidate_stem}.manifest.json"
    [[ "$CANDIDATE_ARCHIVE_NAME" =~ ^qlib_bin\.repair-2026-07-20-[0-9a-f]{40}-[0-9a-f]{64}-[0-9a-f]{64}\.tar\.gz$ ]] \
        || return 1
    [[ "$CANDIDATE_MANIFEST_NAME" =~ ^qlib_bin\.repair-2026-07-20-[0-9a-f]{40}-[0-9a-f]{64}-[0-9a-f]{64}\.manifest\.json$ ]] \
        || return 1

    verify_fixed_release || return 1
    assets="$(list_assets)" || return 1
    original_asset="$(jq -c --argjson id "$FIXED_ORIGINAL_ASSET_ID" \
        '[.[] | select(.id == $id)] | if length == 0 then null elif length == 1 then .[0] else error("duplicate original ID") end' <<<"$assets")" \
        || return 1
    original_present=false
    if [[ "$original_asset" != "null" ]]; then
        original_present=true
        require_original_identity "$original_asset" || return 1
        if ! original_by_id="$(github_get_asset_by_id_optional "$FIXED_ORIGINAL_ASSET_ID")"; then
            die "original asset disappeared during exact-ID refetch"
            return 1
        fi
        require_original_identity "$original_by_id" || return 1
        [[ "$(jq --arg name "$ARCHIVE_NAME" '[.[] | select(.name == $name)] | length' <<<"$assets")" == "1" ]] \
            || { die "canonical archive name is duplicated while original exists"; return 1; }
    fi
    validate_repair_prefix "$assets" "$original_present" || return 1

    original_local="$WORK_DIR/original-archive"
    backup_local="$WORK_DIR/backup-archive"
    if [[ "$original_present" == true ]]; then
        github_download_asset "$FIXED_ORIGINAL_ASSET_ID" "$original_local" || return 1
        verify_file_against_asset "$original_asset" "$original_local" || return 1
        [[ "$(sha256_file "$original_local")" == "$FIXED_ORIGINAL_DIGEST" ]] \
            || { die "original downloaded bytes changed"; return 1; }
    else
        backup_json="$(redownload_named_asset "$BACKUP_NAME" "$backup_local")" || return 1
        [[ "$(file_size "$backup_local")" == "$FIXED_ORIGINAL_SIZE" \
            && "$(sha256_file "$backup_local")" == "$FIXED_ORIGINAL_DIGEST" ]] \
            || { die "backup does not preserve original bytes"; return 1; }
        cp "$backup_local" "$original_local" || return 1
    fi

    candidate_archive_download="$WORK_DIR/candidate-archive"
    candidate_manifest_download="$WORK_DIR/candidate-manifest"
    candidate_archive_json="$(ensure_uploaded_asset "$CANDIDATE_ARCHIVE_NAME" "$archive" application/gzip "$candidate_archive_download")" \
        || return 1
    candidate_manifest_json="$(ensure_uploaded_asset "$CANDIDATE_MANIFEST_NAME" "$manifest" application/json "$candidate_manifest_download")" \
        || return 1
    candidate_archive_json="$(redownload_named_asset "$CANDIDATE_ARCHIVE_NAME" "$candidate_archive_download")" \
        || return 1
    candidate_manifest_json="$(redownload_named_asset "$CANDIDATE_MANIFEST_NAME" "$candidate_manifest_download")" \
        || return 1
    cmp -s "$archive" "$candidate_archive_download" \
        || { die "candidate archive differs from clean rebuild"; return 1; }
    cmp -s "$manifest" "$candidate_manifest_download" \
        || { die "candidate manifest differs from clean rebuild"; return 1; }
    validate_pair "$candidate_archive_download" "$candidate_manifest_download" "$FIXED_TAG" \
        || return 1
    CANDIDATE_ARCHIVE_LOCAL="$candidate_archive_download"
    CANDIDATE_MANIFEST_LOCAL="$candidate_manifest_download"

    backup_json="$(ensure_uploaded_asset "$BACKUP_NAME" "$original_local" application/gzip "$backup_local")" \
        || return 1
    backup_json="$(redownload_named_asset "$BACKUP_NAME" "$backup_local")" || return 1
    cmp -s "$original_local" "$backup_local" \
        || { die "backup bytes differ from original"; return 1; }

    receipt_local="$WORK_DIR/repair-receipt"
    receipt_download="$WORK_DIR/downloaded-receipt"
    create_receipt "$candidate_manifest_download" "$receipt_local" "$backup_json" \
        "$candidate_archive_json" "$candidate_manifest_json" || return 1
    if [[ "$original_present" == true ]]; then
        receipt_phase=pre-delete
        original_by_id="$(github_get_asset_by_id_optional "$FIXED_ORIGINAL_ASSET_ID")" \
            || return 1
        require_original_identity "$original_by_id" || return 1
    else
        receipt_phase=post-delete
        original_by_id=null
    fi
    validate_repair_receipt "$receipt_phase" "$receipt_local" "$candidate_manifest_download" \
        "$original_by_id" "$backup_json" "$candidate_archive_json" "$candidate_manifest_json" \
        null null "$backup_local" "$candidate_archive_download" "$candidate_manifest_download" \
        - - || { die "locally created repair receipt is invalid"; return 1; }
    receipt_json="$(ensure_uploaded_asset "$RECEIPT_NAME" "$receipt_local" application/json "$receipt_download")" \
        || return 1
    receipt_json="$(redownload_named_asset "$RECEIPT_NAME" "$receipt_download")" || return 1
    cmp -s "$receipt_local" "$receipt_download" \
        || { die "repair receipt bytes conflict"; return 1; }

    # The preparation evidence has now been redownloaded and cross-checked.
    if [[ "$original_present" == true ]]; then
        candidate_archive_json="$(redownload_named_asset "$CANDIDATE_ARCHIVE_NAME" "$candidate_archive_download")" \
            || return 1
        candidate_manifest_json="$(redownload_named_asset "$CANDIDATE_MANIFEST_NAME" "$candidate_manifest_download")" \
            || return 1
        backup_json="$(redownload_named_asset "$BACKUP_NAME" "$backup_local")" || return 1
        receipt_json="$(redownload_named_asset "$RECEIPT_NAME" "$receipt_download")" || return 1
        cmp -s "$archive" "$candidate_archive_download" \
            || { die "candidate archive changed before deletion gate"; return 1; }
        cmp -s "$manifest" "$candidate_manifest_download" \
            || { die "candidate manifest changed before deletion gate"; return 1; }
        cmp -s "$original_local" "$backup_local" \
            || { die "backup changed before deletion gate"; return 1; }
        cmp -s "$receipt_local" "$receipt_download" \
            || { die "receipt changed before deletion gate"; return 1; }
        validate_pair "$candidate_archive_download" "$candidate_manifest_download" "$FIXED_TAG" \
            || return 1
        if ! original_by_id="$(github_get_asset_by_id_optional "$FIXED_ORIGINAL_ASSET_ID")"; then
            die "original asset disappeared before the deletion gate"
            return 1
        fi
        require_original_identity "$original_by_id" || return 1
        assets="$(list_assets)" || return 1
        validate_repair_prefix "$assets" true || return 1
        original_by_id="$(slot_json "$assets" "$ARCHIVE_NAME")" || return 1
        require_original_identity "$original_by_id" || return 1
        validate_repair_receipt pre-delete "$receipt_download" "$candidate_manifest_download" \
            "$original_by_id" "$backup_json" "$candidate_archive_json" "$candidate_manifest_json" \
            null null "$backup_local" "$candidate_archive_download" "$candidate_manifest_download" \
            - - || { die "repair receipt/live deletion gate failed"; return 1; }
        delete_original_after_gate || return 1
    fi

    # Re-fetch every prepared asset after the swap boundary.
    candidate_archive_json="$(redownload_named_asset "$CANDIDATE_ARCHIVE_NAME" "$candidate_archive_download")" \
        || return 1
    candidate_manifest_json="$(redownload_named_asset "$CANDIDATE_MANIFEST_NAME" "$candidate_manifest_download")" \
        || return 1
    backup_json="$(redownload_named_asset "$BACKUP_NAME" "$backup_local")" || return 1
    receipt_json="$(redownload_named_asset "$RECEIPT_NAME" "$receipt_download")" || return 1
    cmp -s "$receipt_local" "$receipt_download" \
        || { die "receipt changed before canonical upload"; return 1; }
    validate_pair "$candidate_archive_download" "$candidate_manifest_download" "$FIXED_TAG" \
        || return 1
    validate_repair_receipt post-delete "$receipt_download" "$candidate_manifest_download" \
        null "$backup_json" "$candidate_archive_json" "$candidate_manifest_json" \
        null null "$backup_local" "$candidate_archive_download" "$candidate_manifest_download" \
        - - || { die "repair receipt/live swap gate failed"; return 1; }

    canonical_archive_download="$WORK_DIR/canonical-archive"
    canonical_manifest_download="$WORK_DIR/canonical-manifest"
    ensure_uploaded_asset "$ARCHIVE_NAME" "$candidate_archive_download" application/gzip "$canonical_archive_download" \
        >/dev/null || return 1
    ensure_uploaded_asset "$MANIFEST_NAME" "$candidate_manifest_download" application/json "$canonical_manifest_download" \
        >/dev/null || return 1
    canonical_archive_json="$(redownload_named_asset "$ARCHIVE_NAME" "$canonical_archive_download")" \
        || return 1
    canonical_manifest_json="$(redownload_named_asset "$MANIFEST_NAME" "$canonical_manifest_download")" \
        || return 1
    cmp -s "$candidate_archive_download" "$canonical_archive_download" \
        || { die "canonical archive differs from candidate"; return 1; }
    cmp -s "$candidate_manifest_download" "$canonical_manifest_download" \
        || { die "canonical manifest differs from candidate"; return 1; }
    validate_pair "$canonical_archive_download" "$canonical_manifest_download" "$FIXED_TAG" \
        || return 1
    validate_repair_receipt accepted "$receipt_download" "$candidate_manifest_download" \
        null "$backup_json" "$candidate_archive_json" "$candidate_manifest_json" \
        "$canonical_archive_json" "$canonical_manifest_json" \
        "$backup_local" "$candidate_archive_download" "$candidate_manifest_download" \
        "$canonical_archive_download" "$canonical_manifest_download" \
        || { die "repair receipt/live acceptance gate failed"; return 1; }
    printf 'Repair %s reached Accepted with backup, candidate pair, and receipt retained.\n' "$FIXED_TAG"
}

main() {
    local operation
    if (($# == 0)); then
        operation=publish
    elif (($# == 1)) && [[ "$1" == "repair-2026-07-20" ]]; then
        operation=$1
    else
        usage
        return 2
    fi

    preflight "$operation" || return 1
    WORK_DIR="$(mktemp -d /tmp/investment-data-publisher.XXXXXXXX)" || return 1
    trap 'status=$?; [[ -z "$WORK_DIR" || ! -d "$WORK_DIR" ]] || rm -rf -- "$WORK_DIR"; exit "$status"' EXIT
    if [[ "$operation" == "publish" ]]; then
        publish_current
    else
        repair_fixed_release
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
