#!/bin/bash
curr_dir=$(dirname $0)
python3 $curr_dir/dump_tushare_stock_list.py
dolt table import -u ts_a_stock_list ./stock_list.csv
