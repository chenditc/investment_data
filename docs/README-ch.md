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
- [初衷](#初衷)
- [项目详细信息](#项目详细信息)
  * [数据源](#数据源)
  * [初始导入](#初始导入)
  * [每日更新](#每日更新)
  * [合并逻辑](#合并逻辑)
  * [验证逻辑](#验证逻辑)
- [贡献指南](#贡献指南)
  * [添加更多股票指数](#添加更多股票指数)
  * [添加更多数据源或字段](#添加更多数据源或字段)+

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

# 初衷
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

## 数据表导入以及校验流程
 - [final_a_stock_eod_price](final_a_stock_eod_price.ch.md)


# 贡献指南
## 添加更多股票指数
要添加一个新的股票指数，我们需要改变：
1. 添加指数权重下载脚本。更改[tushare/dump_index_eod_price.py](https://github.com/chenditc/investment_data/blob/main/tushare/dump_index_weight.py#L15) 脚本以导出指数信息。如果指数在tushare中不可用，则编写一个新脚本并添加到[daily_update.sh]([daily_update.sh](https://github.com/chenditc/investment_data/blob/main/daily_update.sh#L12))脚本中。[示例 Pull Request](https://github.com/chenditc/investment_data/commit/a906e4cb1b34d6a63a1b1eda80a4c734a3cd262f)
2. 添加价格下载脚本。更改[tushare/dump_index_eod_price.py](https://github.com/chenditc/investment_data/blob/main/tushare/dump_index_eod_price.py)以添加指数价格。例如[示例 Pull Request](https://github.com/chenditc/investment_data/commit/ae7e0066336fc57dd60d13b20ac456b5358ef91f)
3. 修改导出脚本。更改qlib dump脚本[qlib/dump_index_weight.py#L13](https://github.com/chenditc/investment_data/blob/main/qlib/dump_index_weight.py#L13)，使得指数将被dump并重命名为一个txt文件供使用。[示例 Pull Request](https://github.com/chenditc/investment_data/commit/f41a11c263234587bc40491511ae1822cc509afb)

## 添加更多数据源或字段
请提出一个 Github Issue 来讨论这个计划，包括：
  1. 为什么我们需要这些数据？
  2. 我们如何进行日常更新？
     - 会使用哪个数据源？
     - 应该何时触发更新？
     - 如何验证日常更新已正确完成？
  3. 我们应该从哪个数据源获取历史数据？
  4. 我们如何打算验证历史数据？
     - 数据源是否完整？如何验证的？
     - 数据源是否准确？如何验证的？
     - 如果在验证中发现错误，我们将如何处理？
  5. 是改变现有的表还是添加新的表？

示例 Github Issue：https://github.com/chenditc/investment_data/issues/11

如果数据不干净，我们在此基础上做的工作都就没有可信度。所以我们希望得到的是**高质量**的数据，而不仅仅是**数据**。
