set -e
set -x

[ ! -d "/dolt/investment_data" ] && echo "initializing dolt repo" && cd /dolt && dolt clone chenditc/investment_data
cd /dolt/investment_data
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
dolt sql -q "DELETE FROM ts_a_stock_eod_price WHERE symbol = '000300.SH' AND tradedate = '2004-12-31' AND open = 0 AND high = 0 AND low = 0 AND close = 1000"
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
