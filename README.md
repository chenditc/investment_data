# Setup
## Install dolt
Follow https://github.com/dolthub/dolt

## Clone data

Raw data hosted on dolt: https://www.dolthub.com/repositories/chenditc/investment_data

To download as dolt database:

`dolt clone chenditc/investment_data`

## Export to qlib format
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data bash -c "bash dump_qlib_bin.sh && cp ./qlib_bin.tar.gz /output/"
```

## Daily Update
```
export TUSHARE=<Token>
bash daily_update.sh
```

## Daily update and output
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data bash -c "bash daily_update.sh && bash dump_qlib_bin.sh && cp ./qlib_bin.tar.gz /output/"
```

## Download qlib bin
```
wget https://github.com/chenditc/investment_data/releases/download/20220720/qlib_bin.tar.gz
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=2
```

# Initiative
1. Try to fill in missing data by combining data from multiple data source. For example, delist company's data.
2. Try to correct data by cross validate against multiple data source.

# Data Source

w: high quality static data source 
c: high quality static data source
ts: Tushare data source
ak: Akshare data source
yahoo: Use Qlib's yahoo collector https://github.com/microsoft/qlib/tree/main/scripts/data_collector/yahoo
manual: Some manually entered data

final: Merged final data with validation and correction

# Initial import 

## w
Use one_time_db_scripts to import w_a_stock_eod_price table, used as initial price standard

## c
Use one_time_db_scripts to import c_a_stock_eod_price table, used as a history validation source

## ts
1. Use tushare/update_stock_list.sh to load stock list
2. Use tushare/update_stock_price.sh to load stock price

## yahoo
1. Use yahoo collector to load stock price

# Merge logic
1. Use w data source as baseline, use other data source to validate against it.
2. Since w data's adjclose is different from ts data's adjclose, we will use a **"link date"** to calculate a ratio to map ts adjclose to w adjclose. This can be the maximum first valid data for each data source. The reason we don't use a fixed value for link date is: Some stock might not be trading at specific date, and the enlist and delist date are all different. We store the link date information and adj_ratio in link_table. adj_ratio = link_adj_close / w_adj_close;
3. Append ts data to final dataset, the adjclose will be ts_adj_close / ts_adj_ratio

# Validation logic
1. Generate final data by concatinate w data and ts data.
2. Run validate by pair two data source:
   - Compare high, low, open, close, volume absolute value
   - Calcualte adjclose convert ratio use a link date for each stock.
   - Calculate w data adjclose use link date's ratio, and compare it with final data.

# Contribution
This project is still in early stage and I don't know what's the best way to work together yet. If you are interested in contributing, feel free to email me: chenditc@gmail.com

## Help wanted
1. Currently there is only a stock data, in the future, I would like to add future, fund and bond data.
2. Currently incremental update is using Tushare's data, ideally we want multiple data source to validate against each other.
3. Validate historical data is computation resource expensive, I only validate the absolute value of price for now. Adjust factor is not validated yet.
4. Yahoo data source validation generates a lot of anomaly, I have not fully validate them yet.
