 
中文 README: [![ch](https://img.shields.io/badge/lang-ch-yellow.svg)](https://github.com/chenditc/investment_data/blob/master/docs/README-ch.md)

Chinese blog about this project: [量化系列2 - 众包数据集](https://mp.weixin.qq.com/s/Athd5hsiN_hIKKgxIiO_ow)

- [How to use it](#how-to-use-it)
- [Developement Setup](#developement-setup)
  * [Install dolt](#install-dolt)
  * [Clone data](#clone-data)
  * [Export to qlib format](#export-to-qlib-format)
  * [Run Daily Update](#run-daily-update)
  * [Daily update and output](#daily-update-and-output)
  * [Extract tar file to qlib directory](#extract-tar-file-to-qlib-directory)
- [Initiative](#initiative)
- [Project Detail](#project-detail)
  * [Data Source](#data-source)
  * [Initial import](#initial-import)
  * [Daily Update](#daily-update)
  * [Merge logic](#merge-logic)
  * [Validation logic](#validation-logic)
- [Contribution Guide](#contribution-guide)
  * [Add more stock index](#add-more-stock-index)
  * [Add more data source or fields](#Add-more-data-source-or-fields)

<small><i><a href='http://ecotrust-canada.github.io/markdown-toc/'>Table of contents generated with markdown-toc</a></i></small>


# How to use it
1. Download tar ball from latest release page on github
2. Extract tar file to default qlib directory
```
wget https://github.com/chenditc/investment_data/releases/download/2023-04-20/qlib_bin.tar.gz
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=2
```

# Developement Setup
If you want to contribute to the set of scripts or the data, here is what you should do to set up a dev environment.

## Install dolt
Follow https://github.com/dolthub/dolt

## Clone data
Raw data hosted on dolt: https://www.dolthub.com/repositories/chenditc/investment_data

To download as dolt database:

`dolt clone chenditc/investment_data`

## Export to qlib format
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data bash dump_qlib_bin.sh && cp ./qlib_bin.tar.gz /output/
```

## Run Daily Update
You will need tushare token to use tushare api. Get tushare token from https://tushare.pro/

```
export TUSHARE=<Token>
bash daily_update.sh
```

## Daily update and output
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data bash daily_update.sh && bash dump_qlib_bin.sh && cp ./qlib_bin.tar.gz /output/
```

## Extract tar file to qlib directory
```
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=2
```

# Initiative
1. Try to fill in missing data by combining data from multiple data source. For example, delist company's data.
2. Try to correct data by cross validate against multiple data source.

# Project Detail
## Data Source

The database table on dolthub is named with prefix of data source, for example `ts_a_stock_eod_price`. The meaning of the prefix:

- w(wind): high quality static data source. Only available till 2019.
- c(caihui): high quality static data source. Only available till 2019.
- ts: Tushare data source
- ak: Akshare data source
- yahoo: Use Qlib's yahoo collector https://github.com/microsoft/qlib/tree/main/scripts/data_collector/yahoo
- baostock: [Baostock ](http://baostock.com/)
- final: Merged final data with validation and correction

## Initial loading and Validation logic for each table
 - [final_a_stock_eod_price](docs/final_a_stock_eod_price.md)
 - [final_a_stock_limit](docs/final_a_stock_limit.md)

# Contribution Guide
## Add more stock index
To add a new stock index, we need to change:
1. Add index weight download script. Change [tushare/dump_index_eod_price.py](https://github.com/chenditc/investment_data/blob/main/tushare/dump_index_weight.py#L15) script to dump the index info. If the index is not available in tushare, write a new script and add to the [daily_update.sh]([daily_update.sh](https://github.com/chenditc/investment_data/blob/main/daily_update.sh#L12)) script. [Example commit](https://github.com/chenditc/investment_data/commit/a906e4cb1b34d6a63a1b1eda80a4c734a3cd262f)
2. Add price download script. Change [tushare/dump_index_eod_price.py](https://github.com/chenditc/investment_data/blob/main/tushare/dump_index_eod_price.py) to add the index price. Eg. [Example Commit](https://github.com/chenditc/investment_data/commit/ae7e0066336fc57dd60d13b20ac456b5358ef91f)
3. Modify export script. Change the qlib dump script [qlib/dump_index_weight.py#L13](https://github.com/chenditc/investment_data/blob/main/qlib/dump_index_weight.py#L13), so that index will be dump and renamed to a txt file for use. [Example commit](https://github.com/chenditc/investment_data/commit/f41a11c263234587bc40491511ae1822cc509afb)

## Add more data source or fields
Please raise an issue to discuss the plan, example issue: https://github.com/chenditc/investment_data/issues/11

It should includes:
  1. Why do we want this data?
  2. How do we do regular update?
     - Which data source would we use?
     - When should we trigger update?
     - How do we validate regular update complete correctly?
  2. Which data source should we get historical data?
  3. How do we plan to validate the historical data?
     - Is the data source complete? How did we verify this?
     - Is the data source accurate? How did we verify this?
     - If we see error in validation, how will we deal with them?
  4. Are we changing exisiting table or adding new table? 



If the data is not clean, we might try hard to dig insight from it and find incorrect insight. So we want **high quality** data instead of **just data**.

