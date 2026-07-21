## 初始导入 
- w(wind)：使用one_time_db_scripts导入w_a_stock_eod_price表，作为初始价格标准
- c(caihui)：SQL导入到c_a_stock_eod_price表
- ts(tushare):
  1. 使用tushare/update_stock_list.sh载入股票列表
  2. 使用tushare/update_stock_price.sh载入股票价格
- yahoo
  1. 使用yahoo收集器载入股票价格

## 每日更新
目前，每日更新仅使用tushare数据源，并由github action触发。
1. 我维护了一个离线任务，它每30分钟运行一次[daily_update.sh](daily_update.sh)以收集数据并推送到dolthub。
2. 一个github action [.github/workflows/upload_release.yml](.github/workflows/upload_release.yml)每日触发，然后调用bash dump_qlib_bin.sh生成每日tar文件并上传到发布页面。
   每次发布都必须成对生成规范的 `qlib_bin.tar.gz` 和
   `qlib_bin.manifest.json`。工作流在变更 Release 前校验归档中的实际成员和不可变
   来源信息，并在上传后重新下载、校验这两个资产。`upload_release.sh` 仅供工作流
   内部使用；运维人员只能通过以下命令触发正常发布：

   ```bash
   gh workflow run upload_release.yml --repo chenditc/investment_data --ref main -f operation=publish
   ```

   使用者应下载两个规范资产，先以 `--require-publishable` 运行
   `qlib/validate_archive.py`，再通过 `tar -zxvf qlib_bin.tar.gz -C
   ~/.qlib/qlib_data/cn_data --strip-components=1` 将规范归档解压到 qlib 数据目录。

### 仓库回滚的失效安全顺序

完整仓库回滚必须按以下顺序执行并保持失败关闭：

1. 运行 `gh workflow disable upload_release.yml --repo chenditc/investment_data`。
2. 运行 `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state`，并要求结果严格等于 `disabled_manually`。
3. 查询 `upload_release.yml` 与 `data_update.yml`，等待所有使用共享 Dolt 卷的排队中或运行中任务完全 drain（排空）。
4. 执行完整仓库 revert。
5. 再次运行 `gh workflow view upload_release.yml --repo chenditc/investment_data --json state --jq .state`，并要求状态仍严格等于 `disabled_manually`。
6. 在基于 validator 的摘要固定、工作流 authority、共享 concurrency 和文件锁全部恢复并完成端到端验证前，禁止重新启用发布。

回滚后的便利标签 `latest` 可能影响数据更新，但上传工作流禁用期间不能发布。完整回滚可能移除共享锁与 concurrency group，因此必须先 drain。已经 Accepted 的 Release 资产不会被仓库回滚修改；中断的历史修复只能通过固定的 `repair-2026-07-20` 操作完成；已知陈旧的 backup 永不自动恢复。已部署 monitor 属于独立外部状态，只能另行运行受跟踪的 `ops/investment-data-project-monitor/deploy.sh rollback` 回滚，不能把它耦合到仓库 revert。


## 合并逻辑
1. 使用w数据源作为基准，使用其他数据源进行验证。
2. 由于w数据的adjclose与ts数据的adjclose不同，我们将使用一个**链接日期**来计算比率，以将ts adjclose映射到w adjclose。这可以是每个数据源的最大第一个有效数据。我们不使用固定值作为链接日期的原因是：某些股票可能在特定日期没有交易，而上市和退市日期都不同。我们在link_table中存储链接日期信息和adj_ratio。adj_ratio = link_adj_close / w_adj_close;
3. 将ts数据附加到最终数据集，adjclose将为ts_adj_close / ts_adj_ratio

## 验证逻辑
1. 通过连接w数据和ts数据生成最终数据。
2. 通过配对两个数据源运行验证：
   - 比较高、低、开、收、成交量的绝对值
   - 使用每只股票的链接日期计算adjclose转换比率。
   - 使用链接日期的比率计算w数据的adjclose，并将其与最终数据进行比较。
