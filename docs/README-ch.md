------------------------------------------

关于此项目的中文博客：[量化系列2 - 众包数据集](https://mp.weixin.qq.com/s/Athd5hsiN_hIKKgxIiO_ow)

- [如何使用](#如何使用)
- [开发设置](#开发设置)
  * [安装dolt](#安装dolt)
  * [克隆数据](#克隆数据)
  * [导出为qlib格式](#导出为qlib格式)
  * [运行每日更新](#运行每日更新)
  * [每日更新和输出](#每日更新和输出)
  * [将tar文件解压到qlib目录](#将tar文件解压到qlib目录)
- [倡议](#倡议)
- [项目详细信息](#项目详细信息)
  * [数据源](#数据源)
  * [初始导入](#初始导入)
  * [每日更新](#每日更新)
  * [合并逻辑](#合并逻辑)
  * [验证逻辑](#验证逻辑)
- [贡献指南](#贡献指南)
  * [添加更多股票指数](#添加更多股票指数)

<small><i><a href='http://ecotrust-canada.github.io/markdown-toc/'>使用markdown-toc生成的目录</a></i></small>

# 如何使用
1. 从GitHub上的最新发布页面下载tar压缩文件
2. 将tar文件解压到默认的qlib目录
```
wget https://github.com/chenditc/investment_data/releases/download/2023-04-20/qlib_bin.tar.gz
tar -zxvf qlib_bin.tar.gz -C ~/.qlib/qlib_data/cn_data --strip-components=2
```

# 开发设置
如果你想为这套脚本或数据做出贡献，你应该如何设置开发环境。

## 安装dolt
按照 https://github.com/dolthub/dolt 的指示进行

## 克隆数据
原始数据托管在dolt：https://www.dolthub.com/repositories/chenditc/investment_data

以dolt数据库的形式下载：

`dolt clone chenditc/investment_data`

## 导出为qlib格式
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data bash dump_qlib_bin.sh && cp ./qlib_bin.tar.gz /output/
```

## 运行每日更新
你将需要tushare令牌来使用tushare api。从https://tushare.pro/ 获取tushare令牌。

```
export TUSHARE=<Token>
bash daily_update.sh
```

## 每日更新和输出
```
docker run -v /<some output directory>:/output -it --rm chenditc/investment_data bash daily_update.sh && bash dump_qlib_bin.sh && cp ./qlib_bin.tar.gz /output/
```

## 将tar文件解压到qlib目录
```
tar -zxvf qlib_bin.tar.gz

 -C ~/.qlib/qlib_data/cn_data --strip-components=2
```

# 倡议
1. 尝试通过组合多个数据源来填充缺失的数据，例如退市公司的数据。
2. 尝试通过跨多个数据源进行验证来纠正数据。

# 项目详细信息
## 数据源

dolthub上的数据库表以数据源的前缀命名，例如`ts_a_stock_eod_price`。前缀的含义：

- w(wind)：高质量的静态数据源。只可用到2019年。
- c(caihui)：高质量的静态数据源。只可用到2019年。
- ts：Tushare数据源
- ak：Akshare数据源
- yahoo：使用Qlib的yahoo收集器 https://github.com/microsoft/qlib/tree/main/scripts/data_collector/yahoo

- final：经过验证和校正的最终合并数据

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

## 合并逻辑
1. 使用w数据源作为基准，使用其他数据源进行验证。
2. 由于w数据的adjclose与ts数据的adjclose不同，我们将使用一个**"链接日期"**来计算比率，以将ts adjclose映射到w adjclose。这可以是每个数据源的最大第一个有效数据。我们不使用固定值作为链接日期的原因是：某些股票可能在特定日期没有交易，而上市和退市日期都不同。我们在link_table中存储链接日期信息和adj_ratio。adj_ratio = link_adj_close / w_adj_close;
3. 将ts数据附加到最终数据集，adjclose将为ts_adj_close / ts_adj_ratio

## 验证逻辑
1. 通过连接w数据和ts数据生成最终数据。
2. 通过配对两个数据源运行验证：
   - 比较高、低、开、收、成交量的绝对值
   - 使用每只股票的链接日期计算adjclose转换比率。
   - 使用链接日期的比率计算w数据的adjclose，并将其

与最终数据进行比较。

# 贡献指南
## 添加更多股票指数
要添加一个新的股票指数，我们需要改变：
1. 添加指数权重下载脚本。更改[tushare/dump_index_eod_price.py](https://github.com/chenditc/investment_data/blob/main/tushare/dump_index_weight.py#L15) 脚本以导出指数信息。如果指数在tushare中不可用，则编写一个新脚本并添加到[daily_update.sh]([daily_update.sh](https://github.com/chenditc/investment_data/blob/main/daily_update.sh#L12))脚本中。[示例提交](https://github.com/chenditc/investment_data/commit/a906e4cb1b34d6a63a1b1eda80a4c734a3cd262f)
2. 添加价格下载脚本。更改[tushare/dump_index_eod_price.py](https://github.com/chenditc/investment_data/blob/main/tushare/dump_index_eod_price.py)以添加指数价格。例如[示例提交](https://github.com/chenditc/investment_data/commit/ae7e0066336fc57dd60d13b20ac456b5358ef91f)
3. 修改导出脚本。更改qlib dump脚本[qlib/dump_index_weight.py#L13](https://github.com/chenditc/investment_data/blob/main/qlib/dump_index_weight.py#L13)，使得指数将被dump并重命名为一个txt文件供使用。[示例提交](https://github.com/chenditc/investment_data/commit/f41a11c263234587bc40491511ae1822cc509afb)
