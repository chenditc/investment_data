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
   同样的流程也可以在容器内手动执行，运行[upload_release.sh](../upload_release.sh)并提供`GITHUB_PAT`环境变量即可将生成的tar文件上传到GitHub。

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
