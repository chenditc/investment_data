#!/usr/bin/env bash
set -euo pipefail

usage() {
    printf 'usage: %s [WORKING_DIR [QLIB_REPOSITORY]]\n' "${0##*/}" >&2
}

if (($# > 2)); then
    usage
    exit 2
fi

for rejected in QLIB_BUILD_ID QLIB_BUILD_ROOT CLEAN_QLIB_BUILD_ROOT CHECK_FRESHNESS; do
    if [[ -v "$rejected" ]]; then
        printf 'Error: %s is not a supported dump input.\n' "$rejected" >&2
        exit 2
    fi
done

case "${TRACE:-0}" in
    0) ;;
    1) set -x ;;
    *) printf 'Error: TRACE must be 0 or 1.\n' >&2; exit 2 ;;
esac
if [[ ! "${DUMP_QLIB_MAX_WORKERS:-2}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Error: DUMP_QLIB_MAX_WORKERS must be a positive decimal integer.\n' >&2
    exit 2
fi
DUMP_QLIB_MAX_WORKERS="${DUMP_QLIB_MAX_WORKERS:-2}"

if [[ -v OUTPUT_DIR ]] && { [[ ! -d "$OUTPUT_DIR" ]] || [[ ! -w "$OUTPUT_DIR" ]]; }; then
    printf 'Error: OUTPUT_DIR must be an existing writable directory.\n' >&2
    exit 2
fi

WORKING_ROOT="${1:-/}"
[[ -n "$WORKING_ROOT" ]] || WORKING_ROOT="/"
if [[ ! -d "$WORKING_ROOT" ]]; then
    printf 'Error: working root does not exist: %s\n' "$WORKING_ROOT" >&2
    exit 1
fi
WORKING_ROOT="$(cd "$WORKING_ROOT" && pwd -P)"
QLIB_REPOSITORY="${2:-https://github.com/microsoft/qlib.git}"
INVESTMENT_DATA_DIR="${WORKING_ROOT%/}/investment_data"
DOLT_DIR="${WORKING_ROOT%/}/dolt"
QLIB_REPO_DIR="${WORKING_ROOT%/}/qlib"
SHARED_DOLT_CHECKOUT="${DOLT_DIR}/investment_data"
DOLT_LOCK_FILE="${DOLT_DIR}/.investment-data.lock"
QLIB_COMMIT="b87a2c294d364a33fb739359886acffe8ec907d1"
DOLT_SQL_SERVER_PID=""
BUILD_ROOT=""

if [[ ! -d "$INVESTMENT_DATA_DIR/.git" ]]; then
    printf 'Error: investment_data checkout not found at %s.\n' "$INVESTMENT_DATA_DIR" >&2
    exit 1
fi

publication_values=(
    BUILD_RELEASE_TAG
    BUILD_INVESTMENT_DATA_COMMIT
    BUILD_QLIB_COMMIT
    BUILD_IMAGE_DIGEST
    BUILD_DOLT_COMMIT
)
publication_count=0
for variable in "${publication_values[@]}"; do
    [[ -v "$variable" ]] && ((publication_count += 1))
done
if ((publication_count != 0 && publication_count != ${#publication_values[@]})); then
    printf 'Error: publication build authority must be supplied as one complete tuple.\n' >&2
    exit 2
fi
PUBLISHABLE=false
if ((publication_count == ${#publication_values[@]})); then
    PUBLISHABLE=true
    if [[ "${GITHUB_ACTIONS:-}" != "true" ]] \
        || [[ ! "${BUILD_RELEASE_TAG}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
        || [[ ! "${BUILD_INVESTMENT_DATA_COMMIT}" =~ ^[0-9a-f]{40}$ ]] \
        || [[ "${BUILD_QLIB_COMMIT}" != "$QLIB_COMMIT" ]] \
        || [[ ! "${BUILD_IMAGE_DIGEST}" =~ ^sha256:[0-9a-f]{64}$ ]] \
        || [[ ! "${BUILD_DOLT_COMMIT}" =~ ^[0-9a-v]{32}$ ]]; then
        printf 'Error: malformed publication build authority.\n' >&2
        exit 2
    fi
    if [[ "${PUBLICATION_IMAGE_DIGEST:-}" != "$BUILD_IMAGE_DIGEST" ]] \
        || [[ "${PUBLICATION_BAKED_REPOSITORY_REVISION:-}" != "$BUILD_INVESTMENT_DATA_COMMIT" ]] \
        || [[ "${PUBLICATION_BAKED_QLIB_REVISION:-}" != "$BUILD_QLIB_COMMIT" ]]; then
        printf 'Error: publication build authority does not match launcher evidence.\n' >&2
        exit 1
    fi
fi

cleanup() {
    local status=$?
    if [[ -n "$DOLT_SQL_SERVER_PID" ]]; then
        kill "$DOLT_SQL_SERVER_PID" >/dev/null 2>&1 || true
        wait "$DOLT_SQL_SERVER_PID" >/dev/null 2>&1 || true
    fi
    if [[ -n "$BUILD_ROOT" && -d "$BUILD_ROOT" ]]; then
        rm -rf -- "$BUILD_ROOT"
    fi
    exit "$status"
}
trap cleanup EXIT

log_step() {
    printf '[%s] %s\n' "$(date -Is)" "$*"
}

canonical_date() {
    local value=$1
    [[ "$value" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
        && [[ "$(date -u -d "$value" +%F 2>/dev/null)" == "$value" ]]
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        printf 'Error: required command is unavailable: %s\n' "$1" >&2
        exit 1
    fi
}

for command in dolt find git flock python3 sort tar gzip sha256sum stat mktemp touch; do
    require_command "$command"
done

mkdir -p "$DOLT_DIR"
exec 8>"$DOLT_LOCK_FILE" || {
    printf 'Error: cannot open Dolt checkout lock.\n' >&2
    exit 1
}
if ! flock -n 8; then
    printf 'Error: Dolt checkout lock is held by another process.\n' >&2
    exit 1
fi

if [[ ! -d "$SHARED_DOLT_CHECKOUT/.dolt" ]]; then
    log_step "Cloning Dolt data"
    (cd "$DOLT_DIR" \
        && dolt clone --depth 1 --branch master \
            chenditc/investment_data investment_data)
fi
if ! (cd "$SHARED_DOLT_CHECKOUT" && dolt status) | grep -q 'nothing to commit, working tree clean'; then
    printf 'Error: shared Dolt checkout is not clean.\n' >&2
    exit 1
fi
(cd "$SHARED_DOLT_CHECKOUT" && dolt fetch origin master)

if [[ "$PUBLISHABLE" == true ]]; then
    SELECTED_DOLT_COMMIT="$BUILD_DOLT_COMMIT"
else
    SELECTED_DOLT_COMMIT="$(cd "$SHARED_DOLT_CHECKOUT" \
        && dolt sql -r csv -q "SELECT DOLT_HASHOF('origin/master') AS value" \
        | tail -n 1 | tr -d '\r')"
fi
if [[ ! "$SELECTED_DOLT_COMMIT" =~ ^[0-9a-v]{32}$ ]]; then
    printf 'Error: unable to select an immutable Dolt commit.\n' >&2
    exit 1
fi

BUILD_ROOT="$(mktemp -d "${WORKING_ROOT%/}/.qlib-build.XXXXXXXX")"
SNAPSHOT_DOLT_CHECKOUT="$SHARED_DOLT_CHECKOUT"
(cd "$SNAPSHOT_DOLT_CHECKOUT" && dolt reset --hard "$SELECTED_DOLT_COMMIT")
SNAPSHOT_DOLT_COMMIT="$(cd "$SNAPSHOT_DOLT_CHECKOUT" \
    && dolt sql -r csv -q "SELECT DOLT_HASHOF('HEAD') AS value" \
    | tail -n 1 | tr -d '\r')"
if [[ "$SNAPSHOT_DOLT_COMMIT" != "$SELECTED_DOLT_COMMIT" ]]; then
    printf 'Error: locked Dolt checkout identity mismatch.\n' >&2
    exit 1
fi

if [[ ! -d "$QLIB_REPO_DIR/.git" ]]; then
    git clone "$QLIB_REPOSITORY" "$QLIB_REPO_DIR"
fi
git -C "$QLIB_REPO_DIR" fetch "$QLIB_REPOSITORY" "$QLIB_COMMIT"
if [[ "$(git -C "$QLIB_REPO_DIR" rev-parse FETCH_HEAD)" != "$QLIB_COMMIT" ]]; then
    printf 'Error: supplied qlib repository did not fetch the pinned commit.\n' >&2
    exit 1
fi
git -C "$QLIB_REPO_DIR" checkout --detach FETCH_HEAD
if [[ "$(git -C "$QLIB_REPO_DIR" rev-parse HEAD)" != "$QLIB_COMMIT" ]]; then
    printf 'Error: qlib checkout identity mismatch.\n' >&2
    exit 1
fi

INVESTMENT_DATA_COMMIT="$(git -C "$INVESTMENT_DATA_DIR" rev-parse HEAD)"
if [[ "$PUBLISHABLE" == true && "$INVESTMENT_DATA_COMMIT" != "$BUILD_INVESTMENT_DATA_COMMIT" ]]; then
    printf 'Error: repository checkout does not match publication authority.\n' >&2
    exit 1
fi

if [[ "$PUBLISHABLE" == true ]]; then
    RELEASE_TAG="$BUILD_RELEASE_TAG"
    IMAGE_DIGEST_JSON="\"$BUILD_IMAGE_DIGEST\""
else
    RELEASE_TAG="$(TZ=Asia/Shanghai date +%F)"
    IMAGE_DIGEST_JSON="null"
fi
if ! canonical_date "$RELEASE_TAG"; then
    printf 'Error: release tag is not canonical.\n' >&2
    exit 1
fi

query_scalar() {
    DOLT_QUERY="$1" python3 - <<'PY'
import os
import pymysql

connection = pymysql.connect(
    host="127.0.0.1",
    port=3306,
    user="root",
    password="",
    database="investment_data",
    connect_timeout=2,
)
try:
    with connection.cursor() as cursor:
        cursor.execute(os.environ["DOLT_QUERY"])
        row = cursor.fetchone()
    print("" if row is None or row[0] is None else row[0])
finally:
    connection.close()
PY
}

start_dolt_sql_server() {
    local ready=false server_commit
    (
        cd "$SNAPSHOT_DOLT_CHECKOUT"
        exec dolt sql-server --host 127.0.0.1 --port 3306
    ) >>"$BUILD_ROOT/dolt-sql-server.log" 2>&1 &
    DOLT_SQL_SERVER_PID=$!
    for _ in {1..60}; do
        if [[ "$(query_scalar "SELECT 1" 2>/dev/null || true)" == "1" ]]; then
            ready=true
            break
        fi
        kill -0 "$DOLT_SQL_SERVER_PID" 2>/dev/null || break
        sleep 1
    done
    if [[ "$ready" != true ]] || ! kill -0 "$DOLT_SQL_SERVER_PID" 2>/dev/null; then
        printf 'Error: Dolt SQL server did not become ready.\n' >&2
        exit 1
    fi
    server_commit="$(query_scalar "SELECT DOLT_HASHOF('HEAD')")"
    if [[ "$server_commit" != "$SELECTED_DOLT_COMMIT" ]]; then
        printf 'Error: Dolt SQL server identity mismatch before generation.\n' >&2
        exit 1
    fi
}

stop_dolt_sql_server() {
    if [[ -n "$DOLT_SQL_SERVER_PID" ]]; then
        kill "$DOLT_SQL_SERVER_PID"
        wait "$DOLT_SQL_SERVER_PID"
        DOLT_SQL_SERVER_PID=""
    fi
}

start_dolt_sql_server

TARGET_TRADE_DATE="$(query_scalar "SELECT MAX(date) AS value FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1 AND date <= '$RELEASE_TAG'")"
SOURCE_MAX_DATE="$(query_scalar "SELECT MAX(tradedate) AS value FROM final_a_stock_eod_price")"
FUTURE_START_DATE="$(query_scalar "SELECT MIN(date) AS value FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1 AND date > '$TARGET_TRADE_DATE'")"
FUTURE_END_DATE="$(query_scalar "SELECT MAX(date) AS value FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1")"
for value in "$TARGET_TRADE_DATE" "$SOURCE_MAX_DATE" "$FUTURE_START_DATE" "$FUTURE_END_DATE"; do
    if ! canonical_date "$value"; then
        printf 'Error: snapshot date derivation returned an invalid date.\n' >&2
        exit 1
    fi
done
if [[ "$SOURCE_MAX_DATE" != "$TARGET_TRADE_DATE" ]]; then
    printf 'Error: source max date %s does not equal target trade date %s.\n' \
        "$SOURCE_MAX_DATE" "$TARGET_TRADE_DATE" >&2
    exit 1
fi
if [[ "$RELEASE_TAG" == "2026-07-20" && "$SELECTED_DOLT_COMMIT" == "9vtplc2tar9ver7p6s1bus2oiedjvtqo" ]]; then
    if [[ "$TARGET_TRADE_DATE/$FUTURE_START_DATE/$FUTURE_END_DATE" != "2026-07-20/2026-07-21/2026-12-31" ]]; then
        printf 'Error: fixed repair snapshot dates do not match their captured identity.\n' >&2
        exit 1
    fi
fi

QLIB_SOURCE_DIR="$BUILD_ROOT/qlib_source"
QLIB_NORMALIZE_DIR="$BUILD_ROOT/qlib_normalize"
QLIB_INDEX_DIR="$BUILD_ROOT/qlib_index"
QLIB_BIN_DIR="$BUILD_ROOT/qlib_bin"
mkdir -p "$QLIB_SOURCE_DIR" "$QLIB_NORMALIZE_DIR" "$QLIB_INDEX_DIR" "$QLIB_BIN_DIR"

cd "$INVESTMENT_DATA_DIR"
log_step "Dumping immutable Dolt data to qlib source CSVs"
QLIB_SOURCE_DIR="$QLIB_SOURCE_DIR" python3 ./qlib/dump_all_to_qlib_source.py
QLIB_SOURCE_DIR="$QLIB_SOURCE_DIR" python3 - <<'PY'
import csv
import os
from pathlib import Path

root = Path(os.environ["QLIB_SOURCE_DIR"])
for path in sorted(root.glob("*.csv"), key=lambda value: value.as_posix()):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if not fieldnames or "tradedate" not in fieldnames:
        raise ValueError(f"missing tradedate column: {path}")
    rows.sort(key=lambda row: row["tradedate"])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
PY

log_step "Restarting Dolt SQL server to release export scan cache"
stop_dolt_sql_server
start_dolt_sql_server

export PYTHONPATH="${PYTHONPATH:-}:$QLIB_REPO_DIR:$QLIB_REPO_DIR/scripts"
log_step "Normalizing qlib data with $DUMP_QLIB_MAX_WORKERS workers"
python3 ./qlib/normalize.py \
    --source_dir "$QLIB_SOURCE_DIR" \
    --normalize_dir "$QLIB_NORMALIZE_DIR" \
    --max_workers "$DUMP_QLIB_MAX_WORKERS" \
    --date_field_name tradedate \
    --target_trade_date "$TARGET_TRADE_DATE" \
    --delete_source_after_success=true

if find "$QLIB_SOURCE_DIR" -type f -print -quit | grep -q .; then
    printf 'Error: normalized qlib source files were not cleaned up.\n' >&2
    exit 1
fi
rmdir "$QLIB_SOURCE_DIR"

log_step "Dumping normalized qlib data to binary files sequentially"
QLIB_DUMP_BIN_PATH="$QLIB_REPO_DIR/scripts/dump_bin.py" \
python3 ./qlib/dump_bin_sequential.py \
    --data-path "$QLIB_NORMALIZE_DIR" \
    --qlib-dir "$QLIB_BIN_DIR" \
    --date-field-name tradedate \
    --exclude-fields tradedate,symbol

log_step "Dumping bounded qlib index constituents"
QLIB_INDEX_DIR="$QLIB_INDEX_DIR" python3 ./qlib/dump_index_weight.py \
    --target_trade_date "$TARGET_TRADE_DATE"
cp "$QLIB_INDEX_DIR"/csi*.txt "$QLIB_BIN_DIR/instruments/"

log_step "Dumping snapshot future trade calendar"
python3 ./tushare/dump_day_calendar.py "$QLIB_BIN_DIR" \
    --target_trade_date "$TARGET_TRADE_DATE"

SERVER_DOLT_COMMIT="$(query_scalar "SELECT DOLT_HASHOF('HEAD')")"
if [[ "$SERVER_DOLT_COMMIT" != "$SELECTED_DOLT_COMMIT" ]]; then
    printf 'Error: Dolt SQL server identity mismatch after generation.\n' >&2
    exit 1
fi
stop_dolt_sql_server
flock -u 8
exec 8>&-

SOURCE_DATE_EPOCH="$(date -u -d "$RELEASE_TAG 00:00:00Z" +%s)"
export LC_ALL=C TZ=UTC SOURCE_DATE_EPOCH
while IFS= read -r -d '' path; do chmod 0755 "$path"; done \
    < <(find "$QLIB_BIN_DIR" -type d -print0 | sort -z)
while IFS= read -r -d '' path; do chmod 0644 "$path"; done \
    < <(find "$QLIB_BIN_DIR" -type f -print0 | sort -z)
while IFS= read -r -d '' path; do touch -d "@$SOURCE_DATE_EPOCH" "$path"; done \
    < <(find "$QLIB_BIN_DIR" -type d -print0 | sort -z)
while IFS= read -r -d '' path; do touch -d "@$SOURCE_DATE_EPOCH" "$path"; done \
    < <(find "$QLIB_BIN_DIR" -type f -print0 | sort -z)
if find "$QLIB_BIN_DIR" ! -type d ! -type f -print -quit | grep -q .; then
    printf 'Error: qlib_bin contains a non-regular filesystem entry.\n' >&2
    exit 1
fi

ARCHIVE_PATH="$BUILD_ROOT/qlib_bin.tar.gz"
MANIFEST_PATH="$BUILD_ROOT/qlib_bin.manifest.json"
log_step "Creating deterministic qlib archive"
tar --sort=name --format=ustar --mtime="@$SOURCE_DATE_EPOCH" \
    --owner=0 --group=0 --numeric-owner -cf - -C "$BUILD_ROOT" qlib_bin \
    | gzip -n -9 >"$ARCHIVE_PATH"
ARCHIVE_SIZE="$(stat -c%s "$ARCHIVE_PATH")"
ARCHIVE_SHA256="sha256:$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')"

RELEASE_TAG="$RELEASE_TAG" \
TARGET_TRADE_DATE="$TARGET_TRADE_DATE" \
FUTURE_START_DATE="$FUTURE_START_DATE" \
FUTURE_END_DATE="$FUTURE_END_DATE" \
SELECTED_DOLT_COMMIT="$SELECTED_DOLT_COMMIT" \
INVESTMENT_DATA_COMMIT="$INVESTMENT_DATA_COMMIT" \
QLIB_COMMIT="$QLIB_COMMIT" \
IMAGE_DIGEST_JSON="$IMAGE_DIGEST_JSON" \
ARCHIVE_SIZE="$ARCHIVE_SIZE" \
ARCHIVE_SHA256="$ARCHIVE_SHA256" \
MANIFEST_PATH="$MANIFEST_PATH" \
python3 - <<'PY'
import json
import os

manifest = {
    "release_tag": os.environ["RELEASE_TAG"],
    "target_trade_date": os.environ["TARGET_TRADE_DATE"],
    "future_start_date": os.environ["FUTURE_START_DATE"],
    "future_end_date": os.environ["FUTURE_END_DATE"],
    "dolt_commit": os.environ["SELECTED_DOLT_COMMIT"],
    "investment_data_commit": os.environ["INVESTMENT_DATA_COMMIT"],
    "qlib_commit": os.environ["QLIB_COMMIT"],
    "image_digest": json.loads(os.environ["IMAGE_DIGEST_JSON"]),
    "archive_size_bytes": int(os.environ["ARCHIVE_SIZE"]),
    "archive_sha256": os.environ["ARCHIVE_SHA256"],
}
with open(os.environ["MANIFEST_PATH"], "w", encoding="utf-8", newline="\n") as stream:
    json.dump(manifest, stream, ensure_ascii=False, separators=(",", ":"))
    stream.write("\n")
PY

validator_args=(
    --archive "$ARCHIVE_PATH"
    --manifest "$MANIFEST_PATH"
    --expected-tag "$RELEASE_TAG"
)
if [[ "$PUBLISHABLE" == true ]]; then
    validator_args+=(--require-publishable)
fi
python3 "$INVESTMENT_DATA_DIR/qlib/validate_archive.py" "${validator_args[@]}"

if [[ -v OUTPUT_DIR ]]; then
    DESTINATION_DIR="$OUTPUT_DIR"
elif [[ -d /output ]]; then
    DESTINATION_DIR=/output
else
    DESTINATION_DIR="$INVESTMENT_DATA_DIR"
fi
mv "$ARCHIVE_PATH" "$DESTINATION_DIR/qlib_bin.tar.gz"
mv "$MANIFEST_PATH" "$DESTINATION_DIR/qlib_bin.manifest.json"
printf 'Generated validated archive pair at %s\n' "$DESTINATION_DIR"
