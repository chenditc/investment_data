set -e
set -x
WORKING_DIR=${1} 

if ! command -v dolt &> /dev/null
then
    curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash
fi

mkdir -p $WORKING_DIR/dolt

[ ! -d "$WORKING_DIR/dolt/investment_data" ] && cd $WORKING_DIR/dolt && dolt clone chenditc/investment_data

cd $WORKING_DIR/dolt/investment_data
dolt pull origin
dolt sql-server &

# wait for sql server start
sleep 5s

cd $WORKING_DIR/investment_data
mkdir ./qlib/qlib_source
python3 ./qlib/dump_all_to_qlib_source.py

export PYTHONPATH=$PYTHONPATH:/qlib/scripts
python3 ./qlib/normalize.py normalize_data --source_dir ./qlib/qlib_source/ --normalize_dir ./qlib_normalize --max_workers=16 --date_field_name="tradedate" 
python3 $WORKING_DIR/qlib/scripts/dump_bin.py dump_all --csv_path ./qlib_normalize/ --qlib_dir ./qlib_bin --date_field_name=tradedate --exclude_fields=tradedate,symbol

mkdir ./qlib/qlib_index/
python3 ./qlib/dump_index_weight.py 
python3 ./tushare/dump_day_calendar.py ./qlib_bin/
killall dolt

cp qlib/qlib_index/csi* ./qlib_bin/instruments/

tar -czvf ./qlib_bin.tar.gz ./qlib_bin/
