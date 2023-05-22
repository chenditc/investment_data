cd /investment_data/
dolt pull origin

dolt sql-server &
mkdir ./qlib/qlib_source
python3 ./qlib/dump_all_to_qlib_source.py
killall dolt

export PYTHONPATH=$PYTHONPATH:/qlib/scripts
python3 ./qlib/normalize.py normalize_data --source_dir ./qlib/qlib_source/ --normalize_dir ./qlib_normalize --max_workers=16 --date_field_name="tradedate" 
python3 /qlib/scripts/dump_bin.py dump_all --csv_path ./qlib_normalize/ --qlib_dir ./qlib_bin --date_field_name=tradedate --exclude_fields=tradedate,symbol

dolt sql-server &
mkdir ./qlib/qlib_index/
python3 ./qlib/dump_index_weight.py 
python3 /qlib/scripts/data_collector/future_calendar_collector.py --qlib_dir ./qlib_bin/ --region cn
killall dolt

cp qlib/qlib_index/csi* ./qlib_bin/instruments/

tar -czvf ./qlib_bin.tar.gz ./qlib_bin/
