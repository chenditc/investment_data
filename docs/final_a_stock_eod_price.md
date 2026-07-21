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
   Publication always produces the canonical `qlib_bin.tar.gz` and
   `qlib_bin.manifest.json` pair. The workflow validates actual archive members
   and immutable provenance before mutation, then redownloads and validates the
   published pair. `upload_release.sh` is workflow-internal; operators dispatch
   normal publication only with:

   ```bash
   gh workflow run upload_release.yml --repo chenditc/investment_data --ref main -f operation=publish
   ```

   Consumers download both canonical assets, run `qlib/validate_archive.py`
   with `--require-publishable`, then extract `qlib_bin.tar.gz` into the qlib
   data directory with `tar -zxvf qlib_bin.tar.gz -C
   ~/.qlib/qlib_data/cn_data --strip-components=1`.

### Fail-safe repository rollback

A full repository revert is ordered and fail-closed:

1. Run `gh workflow disable upload_release.yml --repo chenditc/investment_data`.
2. Query `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state` and require the exact result `disabled_manually`.
3. Query both `upload_release.yml` and `data_update.yml` runs and wait until every queued or in-progress job using the shared Dolt volume has drained.
4. Perform the full repository revert.
5. Run `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state` again and require `disabled_manually`.
6. Do not re-enable publication until validator-backed digest pinning, workflow authority, and shared concurrency/filesystem locking are restored and verified end to end.

The revert may move the convenience `latest` image and therefore affect data update, but it cannot publish while the upload workflow is disabled. Draining is mandatory because a full revert may remove the shared lock and concurrency group. Already accepted release assets are untouched. An interrupted historical repair may complete only through the fixed `repair-2026-07-20` operation, and the stale backup is never auto-restored. The deployed monitor is separate external state; roll it back only with the tracked `ops/investment-data-project-monitor/deploy.sh rollback`, never as part of the repository revert.


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
