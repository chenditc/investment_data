set -e
set -x
WORKING_DIR=${1} 
QLIB_REPO=${2:-https://github.com/microsoft/qlib.git} 

if ! command -v dolt &> /dev/null
then
    curl -L https://github.com/dolthub/dolt/releases/latest/download/install.sh | bash
fi

mkdir -p $WORKING_DIR/dolt

[ ! -d "$WORKING_DIR/dolt/investment_data" ] && cd $WORKING_DIR/dolt && dolt clone chenditc/investment_data
[ ! -d "$WORKING_DIR/qlib" ] && git clone $QLIB_REPO "$WORKING_DIR/qlib"

cd $WORKING_DIR/dolt/investment_data
dolt fetch origin master
dolt reset --hard origin/master
dolt sql-server &

# wait for sql server start
sleep 5s

cd $WORKING_DIR/investment_data
rm -rf "$WORKING_DIR/qlib_bin" ./qlib/qlib_source ./qlib/qlib_normalize ./qlib/qlib_index
mkdir -p ./qlib/qlib_source
python3 ./qlib/dump_all_to_qlib_source.py

export PYTHONPATH=$PYTHONPATH:$WORKING_DIR/qlib/scripts
cd ./qlib
python3 ./normalize.py normalize_data --source_dir ./qlib_source/ --normalize_dir ./qlib_normalize --max_workers=16 --date_field_name="tradedate" 
python3 $WORKING_DIR/qlib/scripts/dump_bin.py dump_all --data_path ./qlib_normalize/ --qlib_dir $WORKING_DIR/qlib_bin --date_field_name=tradedate --exclude_fields=tradedate,symbol

mkdir -p ./qlib_index/
python3 ./dump_index_weight.py 

cd $WORKING_DIR/investment_data
python3 ./tushare/dump_day_calendar.py $WORKING_DIR/qlib_bin/

if [ "${CHECK_FRESHNESS:-0}" = "1" ]; then
    cd $WORKING_DIR/dolt/investment_data
    source_max_date=$(dolt sql -r csv -q "SELECT MAX(tradedate) AS max_date FROM final_a_stock_eod_price" | tail -n 1)
    expected_max_date=$(dolt sql -r csv -q "SELECT MAX(date) AS max_date FROM ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1 AND date <= CURRENT_DATE" | tail -n 1)
    qlib_max_date=$(tail -n 1 "$WORKING_DIR/qlib_bin/calendars/day.txt")

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
    cd $WORKING_DIR/investment_data
fi

killall dolt

cp qlib/qlib_index/csi* $WORKING_DIR/qlib_bin/instruments/

tar -czvf ./qlib_bin.tar.gz $WORKING_DIR/qlib_bin/
ls -lh ./qlib_bin.tar.gz
OUTPUT_DIR=${OUTPUT_DIR:-/output}
if [ -d "${OUTPUT_DIR}" ]; then
    mv ./qlib_bin.tar.gz "${OUTPUT_DIR}/"
    ls -lh "${OUTPUT_DIR}/qlib_bin.tar.gz"
else
    echo "Generated tarball at $(pwd)/qlib_bin.tar.gz"
fi
