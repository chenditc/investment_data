set -e
set -x

dolt pull origin

echo "Updating index weight"
startdate=$(dolt sql -q "select * from max_index_date" -r csv | tail -1)
python3 tushare/dump_index_weight.py --start_date=$startdate
for file in $(ls tushare/index_weight/); 
do  
  dolt table import -u ts_index_weight tushare/index_weight/$file; 
done

echo "Updating index price"
python3 tushare/dump_index_eod_price.py 
for file in $(ls tushare/index/); 
do   
  dolt table import -u ts_a_stock_eod_price tushare/index/$file; 
done

echo "Updating stock price"
dolt sql-server &
python3 tushare/update_a_stock_eod_price_to_latest.py
killall dolt

dolt sql --file ./tushare/regular_update.sql

dolt add -A

dolt commit -m "Daily update"

dolt push
