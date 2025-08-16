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
dolt pull origin
dolt sql-server &

# wait for sql server start
sleep 5s

cd $WORKING_DIR/investment_data
mkdir -p ./qlib/qlib_source
python3 ./qlib/dump_all_to_qlib_source.py

export PYTHONPATH=$PYTHONPATH:$WORKING_DIR/qlib/scripts
python3 ./qlib/normalize.py normalize_data --source_dir ./qlib/qlib_source/ --normalize_dir ./qlib/qlib_normalize --max_workers=16 --date_field_name="tradedate" 
python3 $WORKING_DIR/qlib/scripts/dump_bin.py dump_all --data_path ./qlib/qlib_normalize/ --qlib_dir $WORKING_DIR/qlib_bin --date_field_name=tradedate --exclude_fields=tradedate,symbol

mkdir -p ./qlib/qlib_index/
python3 ./qlib/dump_index_weight.py 
python3 ./tushare/dump_day_calendar.py $WORKING_DIR/qlib_bin/
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
