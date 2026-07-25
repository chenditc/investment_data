set -euo pipefail
set -x

DOLT_DIR="/dolt"
DOLT_LOCK_FILE="${DOLT_DIR}/.investment-data.lock"
if ! command -v flock >/dev/null 2>&1; then
    echo "Error: flock is required for the shared Dolt checkout." >&2
    exit 1
fi
mkdir -p "${DOLT_DIR}"
exec 8>"${DOLT_LOCK_FILE}"
if ! flock -n 8; then
    echo "Error: shared Dolt checkout is locked by another workflow." >&2
    exit 1
fi

[ ! -d "${DOLT_DIR}/investment_data" ] \
  && echo "initializing shallow dolt repo" \
  && cd "${DOLT_DIR}" \
  && dolt clone --depth 1 --branch master chenditc/investment_data investment_data
cd "${DOLT_DIR}/investment_data"
dolt fetch origin master
dolt reset --hard origin/master
dolt checkout .

echo "Updating index weight"
# Keep this code list in sync with tushare/dump_index_weight.py.
# We intentionally ignore stale legacy rows such as 000300.SH so they
# do not drag the shared backfill start date back to 2022.
startdate=$(dolt sql -q "
SELECT MIN(index_max_date) AS start_date
FROM (
  SELECT DATE_FORMAT(DATE_ADD(COALESCE(MAX(trade_date), '19900101'), INTERVAL 1 DAY), '%Y%m%d') AS index_max_date
  FROM ts_index_weight
  WHERE index_code = '000905.SH'
  UNION ALL
  SELECT DATE_FORMAT(DATE_ADD(COALESCE(MAX(trade_date), '19900101'), INTERVAL 1 DAY), '%Y%m%d') AS index_max_date
  FROM ts_index_weight
  WHERE index_code = '399300.SZ'
  UNION ALL
  SELECT DATE_FORMAT(DATE_ADD(COALESCE(MAX(trade_date), '19900101'), INTERVAL 1 DAY), '%Y%m%d') AS index_max_date
  FROM ts_index_weight
  WHERE index_code = '000906.SH'
  UNION ALL
  SELECT DATE_FORMAT(DATE_ADD(COALESCE(MAX(trade_date), '19900101'), INTERVAL 1 DAY), '%Y%m%d') AS index_max_date
  FROM ts_index_weight
  WHERE index_code = '000852.SH'
  UNION ALL
  SELECT DATE_FORMAT(DATE_ADD(COALESCE(MAX(trade_date), '19900101'), INTERVAL 1 DAY), '%Y%m%d') AS index_max_date
  FROM ts_index_weight
  WHERE index_code = '000985.SH'
) current_index_weight_dates
" -r csv | tail -1)
python3 /investment_data/tushare/dump_index_weight.py --start_date=$startdate
for file in $(ls /investment_data/tushare/index_weight/); 
do  
  dolt table import -u ts_index_weight /investment_data/tushare/index_weight/$file; 
done

echo "Updating index price"
python3 /investment_data/tushare/dump_index_eod_price.py 
dolt sql -q "
DELETE FROM ts_a_stock_eod_price
WHERE symbol IN ('399300.SZ', '000905.SH', '000300.SH', '000906.SH', '000852.SH', '000985.SH')
  AND (
    open IS NULL
    OR high IS NULL
    OR low IS NULL
    OR close IS NULL
    OR volume IS NULL
    OR amount IS NULL
    OR (
      tradedate = '2004-12-31'
      AND close = 1000
      AND (open = 0 OR high = 0 OR low = 0 OR volume = 0 OR amount = 0)
    )
  )
"
for file in $(ls /investment_data/tushare/index/); 
do   
  dolt table import -u ts_a_stock_eod_price /investment_data/tushare/index/$file; 
done

echo "Updating stock price"
dolt sql-server &
sleep 5 && python3 /investment_data/tushare/update_a_stock_eod_price_to_latest.py
killall dolt

dolt sql --file /investment_data/tushare/regular_update.sql

dolt add -A

status_output=$(dolt status)

# Check if the status output contains the "nothing to commit, working tree clean" message
if [[ $status_output == *"nothing to commit, working tree clean"* ]]; then
    echo "No changes to commit. Working tree is clean."
else
    echo "Changes found. Committing and pushing..."
    # Run the necessary commands
    dolt commit -m "Daily update"
    dolt push --force origin master
    echo "Changes committed and pushed."
fi
