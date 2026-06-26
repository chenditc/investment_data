#!/usr/bin/env bash
set -euo pipefail

if [ "${TRACE:-0}" = "1" ]; then
    set -x
fi

WORKING_DIR=${1:-}
QLIB_REPO=${2:-https://github.com/microsoft/qlib.git}
INVESTMENT_DATA_DIR="${WORKING_DIR}/investment_data"
DOLT_DIR="${WORKING_DIR}/dolt"
QLIB_REPO_DIR="${WORKING_DIR}/qlib"
RUN_ID="${QLIB_BUILD_ID:-$(date +%Y%m%d%H%M%S)-$$}"
BUILD_ROOT="${QLIB_BUILD_ROOT:-${WORKING_DIR}/qlib_build_${RUN_ID}}"
QLIB_SOURCE_DIR="${BUILD_ROOT}/qlib_source"
QLIB_NORMALIZE_DIR="${BUILD_ROOT}/qlib_normalize"
QLIB_INDEX_DIR="${BUILD_ROOT}/qlib_index"
QLIB_BIN_DIR="${BUILD_ROOT}/qlib_bin"
DUMP_QLIB_MAX_WORKERS="${DUMP_QLIB_MAX_WORKERS:-8}"
DOLT_SQL_SERVER_PID=""

cleanup() {
    if [ -n "${DOLT_SQL_SERVER_PID}" ]; then
        kill "${DOLT_SQL_SERVER_PID}" >/dev/null 2>&1 || true
        wait "${DOLT_SQL_SERVER_PID}" >/dev/null 2>&1 || true
    fi
    if [ "${CLEAN_QLIB_BUILD_ROOT:-1}" = "1" ]; then
        rm -rf "${BUILD_ROOT}"
    fi
}
trap cleanup EXIT

log_step() {
    echo "[$(date -Is)] $*"
}

if ! command -v dolt &> /dev/null
then
    curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash
fi

mkdir -p "${DOLT_DIR}"

[ ! -d "${DOLT_DIR}/investment_data" ] && cd "${DOLT_DIR}" && dolt clone chenditc/investment_data
[ ! -d "${QLIB_REPO_DIR}" ] && git clone "${QLIB_REPO}" "${QLIB_REPO_DIR}"

cd "${DOLT_DIR}/investment_data"
log_step "Fetching latest Dolt data"
dolt fetch origin master
dolt reset --hard origin/master
dolt sql-server &
DOLT_SQL_SERVER_PID="$!"

# wait for sql server start
sleep 5s

cd "${INVESTMENT_DATA_DIR}"
rm -rf "${BUILD_ROOT}"
mkdir -p "${QLIB_SOURCE_DIR}" "${QLIB_NORMALIZE_DIR}" "${QLIB_INDEX_DIR}" "${QLIB_BIN_DIR}"
log_step "Dumping Dolt data to qlib source CSVs"
QLIB_SOURCE_DIR="${QLIB_SOURCE_DIR}" python3 ./qlib/dump_all_to_qlib_source.py

export PYTHONPATH="${PYTHONPATH:-}:${QLIB_REPO_DIR}/scripts"
cd ./qlib
log_step "Normalizing qlib data with ${DUMP_QLIB_MAX_WORKERS} workers"
python3 ./normalize.py normalize_data --source_dir "${QLIB_SOURCE_DIR}/" --normalize_dir "${QLIB_NORMALIZE_DIR}" --max_workers="${DUMP_QLIB_MAX_WORKERS}" --date_field_name="tradedate"
log_step "Dumping normalized qlib data to binary files"
python3 "${QLIB_REPO_DIR}/scripts/dump_bin.py" dump_all --csv_path "${QLIB_NORMALIZE_DIR}/" --qlib_dir "${QLIB_BIN_DIR}" --date_field_name=tradedate --exclude_fields=tradedate,symbol

mkdir -p "${QLIB_INDEX_DIR}"
log_step "Dumping qlib index constituents"
QLIB_INDEX_DIR="${QLIB_INDEX_DIR}" python3 ./dump_index_weight.py

cd "${INVESTMENT_DATA_DIR}"
log_step "Dumping qlib trade calendar"
python3 ./tushare/dump_day_calendar.py "${QLIB_BIN_DIR}/"

if [ "${CHECK_FRESHNESS:-0}" = "1" ]; then
    cd "${DOLT_DIR}/investment_data"
    source_max_date=$(dolt sql -r csv -q "SELECT MAX(tradedate) AS max_date FROM final_a_stock_eod_price" | tail -n 1)
    expected_max_date=$(dolt sql -r csv -q "SELECT MAX(date) AS max_date FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1 AND date <= CURRENT_DATE" | tail -n 1)
    qlib_max_date=$(tail -n 1 "${QLIB_BIN_DIR}/calendars/day.txt")

    echo "Expected latest trade date: ${expected_max_date}"
    echo "Dolt source latest trade date: ${source_max_date}"
    echo "Qlib archive latest trade date: ${qlib_max_date}"

    if [ "$source_max_date" != "$expected_max_date" ]; then
        echo "Dolt source data is stale; refusing to publish release." >&2
        exit 1
    fi

    if [ "$qlib_max_date" != "$source_max_date" ]; then
        echo "Qlib archive is stale; refusing to publish release." >&2
        exit 1
    fi
    cd "${INVESTMENT_DATA_DIR}"
fi

kill "${DOLT_SQL_SERVER_PID}" >/dev/null 2>&1 || true
wait "${DOLT_SQL_SERVER_PID}" >/dev/null 2>&1 || true
DOLT_SQL_SERVER_PID=""

cp "${QLIB_INDEX_DIR}"/csi* "${QLIB_BIN_DIR}/instruments/"

log_step "Creating qlib_bin.tar.gz"
tar -czvf ./qlib_bin.tar.gz -C "${BUILD_ROOT}" qlib_bin/
ls -lh ./qlib_bin.tar.gz
OUTPUT_DIR=${OUTPUT_DIR:-/output}
if [ -d "${OUTPUT_DIR}" ]; then
    mv ./qlib_bin.tar.gz "${OUTPUT_DIR}/"
    ls -lh "${OUTPUT_DIR}/qlib_bin.tar.gz"
else
    echo "Generated tarball at $(pwd)/qlib_bin.tar.gz"
fi
