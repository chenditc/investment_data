 
中文 README: [![ch](https://img.shields.io/badge/lang-ch-yellow.svg)](https://github.com/chenditc/investment_data/blob/master/docs/README-ch.md)

Chinese blog about this project: [量化系列2 - 众包数据集](https://mp.weixin.qq.com/s/Athd5hsiN_hIKKgxIiO_ow)

- [How to use it](#how-to-use-it)
- [Developement Setup](#developement-setup)
  - [Install dolt](#install-dolt)
  - [Clone data](#clone-data)
  - [Export to qlib format](#export-to-qlib-format)
  - [Run Daily Update](#run-daily-update)
  - [Daily update and output](#daily-update-and-output)
  - [Extract tar file to qlib directory](#extract-tar-file-to-qlib-directory)
- [Initiative](#initiative)
- [Project Detail](#project-detail)
  - [Data Source](#data-source)
  - [Initial loading and Validation logic for each table](#initial-loading-and-validation-logic-for-each-table)
- [Contribution Guide](#contribution-guide)
  - [Add more stock index](#add-more-stock-index)
  - [Add more data source or fields](#add-more-data-source-or-fields)

<small><i><a href='http://ecotrust-canada.github.io/markdown-toc/'>Table of contents generated with markdown-toc</a></i></small>

# Special thanks
To: [dmnsn7](https://github.com/dmnsn7) Who provided tushare token and make daily update possible.

# Sponsor
To continue maintain the CI/CD pipeline for this project, we need VPS with 30G+ memory, 4 core+ CPU and good connection to dolthub and github.

If you are willing to sponsor the running environment, please contact me directly.

If you are willing to sponsor the VPS fee, you can donate in this page: https://mp.weixin.qq.com/s/Athd5hsiN_hIKKgxIiO_ow

Thanks for everyone's help.

# How to use it
1. Download the canonical archive and its provenance manifest from the same GitHub release.
2. Validate the completed archive bytes and date-bearing members.
3. Extract the canonical archive to the default qlib directory.
```
wget "https://github.com/chenditc/investment_data/releases/download/<release-tag>/qlib_bin.tar.gz"
wget "https://github.com/chenditc/investment_data/releases/download/<release-tag>/qlib_bin.manifest.json"
python3 qlib/validate_archive.py \
  --archive qlib_bin.tar.gz \
  --manifest qlib_bin.manifest.json \
  --expected-tag "<release-tag>" \
  --require-publishable
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=1
```

Index instruments files contain `symbol`, inclusive start date, and inclusive
end date. Continuous membership is stored as one interval; a symbol can have
multiple rows if it leaves the index and later returns. Historical members
remain in the file even when they are absent from the latest index snapshot.

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
docker run \
  -v /<some output directory>:/output \
  -it --rm chenditc/investment_data bash dump_qlib_bin.sh
```

To build a validated package after a source-data repair without replacing an
existing dated release, dispatch `upload_release.yml` on `main` with
`operation=build`. The workflow uses the verified immutable image and publisher
preflight, selects the current Dolt snapshot, and uploads both canonical files
as a `qlib-source-repair-<run-id>` Actions artifact. Its manifest records the
image digest and source commits. The artifact is retained for 14 days; normal
scheduled publication continues to create dated releases.

The standalone diagnostic grammar is `bash dump_qlib_bin.sh [WORKING_DIR [QLIB_REPOSITORY]]`.
It always creates and validates `qlib_bin.tar.gz` together with
`qlib_bin.manifest.json`. A standalone manifest has `image_digest:null`, so it
is suitable for local inspection but cannot be published as a release asset.

You can use the following parameter to mount an existing dolt chenditc/investment_data folder to the container.
```
  -v /<dolt directory>:/dolt 
```

## Run Daily Update
You will need tushare token to use tushare api. Get tushare token from https://tushare.pro/

```
export TUSHARE=<Token>
bash daily_update.sh
```

The stock collector uses Asia/Shanghai dates and waits until 17:00 before
collecting the current trading day's closing prices. Failed requests, missing
expected trading days, and incomplete price/adjustment-factor pairs stop the
update before it can commit or advance past a missing day.

## Daily update and output
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data \
  bash -lc 'bash daily_update.sh && bash dump_qlib_bin.sh'
```

## Upload to GitHub release
Release publication is authorized only by the digest-pinned workflow on `main`.
Dispatch the normal publisher with:

```
gh workflow run upload_release.yml --repo chenditc/investment_data --ref main -f operation=publish
```

`upload_release.sh` is workflow-internal and rejects direct/local/container
publication. The workflow validates the ten-field manifest and the complete
archive before any release mutation, then redownloads and validates both
canonical assets.

The release gate uses a bounded, read-only DoltHub GraphQL query to avoid
unnecessary builds while data is still stale. If that online check is unavailable,
it defers to the publisher's mandatory local snapshot freshness and archive
validation; a failed local check still prevents release creation and uploads.
The target trading day must also contain more than 1,000 price rows, matching
the collector's existing completion floor, so index-only updates cannot make
an otherwise stale stock snapshot publishable.

The fixed historical operation `repair-2026-11-08` repairs issue #163 using
the pinned corrected Dolt commit. It runs only through a manual `main`
workflow, verifies the captured original release/asset identities, preserves
and redownloads both originals in a separate backup prerelease, stages and
validates the new pair, then replaces the canonical names and validates the
downloaded result. Prepared and accepted receipts remain in the backup release.

### Fail-safe repository rollback

A full repository revert is ordered and fail-closed:

1. Run `gh workflow disable upload_release.yml --repo chenditc/investment_data`.
2. Query `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state` and require the exact result `disabled_manually`.
3. Query both `upload_release.yml` and `data_update.yml` runs and wait until every queued or in-progress job using the shared Dolt volume has drained.
4. Perform the full repository revert.
5. Run `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state` again and require `disabled_manually`.
6. Do not re-enable publication until validator-backed digest pinning, workflow authority, and shared concurrency/filesystem locking are restored and verified end to end.

The revert may move the convenience `latest` image and therefore affect data update, but it cannot publish while the upload workflow is disabled. Draining is mandatory because a full revert may remove the shared lock and concurrency group. Already accepted release assets are untouched. An interrupted historical repair may complete only through the fixed `repair-2026-07-20` operation, and the stale backup is never auto-restored. The deployed monitor is separate external state; roll it back only with the tracked `ops/investment-data-project-monitor/deploy.sh rollback`, never as part of the repository revert.


## Extract tar file to qlib directory
```
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=1
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

## Dolt Table Explanation
#### ts_link_table
The initial date for each stock might be different, when we calculate the adjusted price, we are using the first date price as adjust factor = 1.0.

In order to merge different data sources, we need to rescale the adjust factor, so that each data source will have the same adjusted price.

Each data source will have a dedicated link table, which is generated by:
1. If the final_a_stock_eod_price already has this stock, adjust_ratio = final_a_stock_eod_price.adjust_price / current_data_source.adjust_price
2. If the stock is new to final_a_stock_eod_price, adjust_ratio = 1.

Data validation needs to run to verify if the adjust factor match between each data source:
 - data_source_1.adjust_ratio * data_source_1.adjust_price = final_a_stock_eod_price.adjust_price 

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
