import tushare as ts
import os
import datetime
import pandas
ts.set_token(os.environ["TUSHARE"])
pro=ts.pro_api()
d_data = pro.stock_basic(list_status="D", fields=["ts_code","symbol","exchange","list_date","delist_date"])
d_data["delist_date"] = pandas.to_datetime(d_data["delist_date"], format="%Y%m%d")
d_data["delist_date"] = d_data["delist_date"].dt.strftime("%Y-%m-%d")

l_data = pro.stock_basic(list_status="L", fields=["ts_code","symbol","exchange","list_date","delist_date"])

data = pandas.concat([d_data, l_data])
data["list_date"] = pandas.to_datetime(data["list_date"], format="%Y%m%d")
data["list_date"] = data["list_date"].dt.strftime("%Y-%m-%d")

data.to_csv("stock_list.csv", index=False)
