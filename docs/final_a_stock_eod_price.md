## Initial import 

- w(wind): Use one_time_db_scripts to import w_a_stock_eod_price table, used as initial price standard
- c(caihui): SQL import to c_a_stock_eod_price table
- ts(tushare):
  1. Use tushare/update_stock_list.sh to load stock list
  2. Use tushare/update_stock_price.sh to load stock price
- yahoo
  1. Use yahoo collector to load stock price

## Daily Update
Currently the daily update is only using tushare data source and triggered by github action.
1. I maintained a offline job whcih runs [daily_update.sh](daily_update.sh) every 30 mins to collect data and push to dolthub.
2. A github action [.github/workflows/upload_release.yml](.github/workflows/upload_release.yml) is triggered daily, which then calls bash dump_qlib_bin.sh to generate daily tar file and upload to release page.
   The same process can be executed manually inside the container by running [upload_release.sh](../upload_release.sh), which expects a `GITHUB_PAT` environment variable and uploads the generated tarball to GitHub.

## Merge logic
1. Use w data source as baseline, use other data source to validate against it.
2. Since w data's adjclose is different from ts data's adjclose, we will use a **"link date"** to calculate a ratio to map ts adjclose to w adjclose. This can be the maximum first valid data for each data source. The reason we don't use a fixed value for link date is: Some stock might not be trading at specific date, and the enlist and delist date are all different. We store the link date information and adj_ratio in link_table. adj_ratio = link_adj_close / w_adj_close;
3. Append ts data to final dataset, the adjclose will be ts_adj_close / ts_adj_ratio

## Validation logic
1. Generate final data by concatinate w data and ts data.
2. Run validate by pair two data source:
   - Compare high, low, open, close, volume absolute value
   - Calcualte adjclose convert ratio use a link date for each stock.
   - Calculate w data adjclose use link date's ratio, and compare it with final data.
