import tushare as ts
import os
import datetime
import pandas
import fire
import time
from sqlalchemy import create_engine
import pymysql

ts.set_token(os.environ["TUSHARE"])
pro=ts.pro_api()

def get_trade_cal(start_date, end_date):
    df = pro.trade_cal(exchange='SSE', is_open='1',
                            start_date=start_date,
                            end_date=end_date,
                            fields='cal_date')
    return df

def get_daily(trade_date):
    for _ in range(3):
        try:
            price_df = pro.daily(trade_date=trade_date)
            adj_factor = pro.adj_factor(trade_date=trade_date)
            df = pandas.merge(price_df, adj_factor, on="ts_code", how="inner")
            df["adj_close"] = df["close"] * df["adj_factor"]
            return df
        except Exception as e:
            print(e)
            time.sleep(1)

def dump_astock_data():
    sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
    dbConnection = sqlEngine.connect()

    sql = """
    select max(tradedate) as tradedate
        FROM
        (select tradedate, count(tradedate) as symbol_count 
        FROM ts_a_stock_eod_price 
        where tradedate > "2022-07-01" 
        group by tradedate) tradedate_record
    WHERE symbol_count > 1000
    """
    latest_trade_date = pandas.read_sql(sql, dbConnection)["tradedate"][0].strftime('%Y%m%d')
    end_date = datetime.datetime.now().strftime('%Y%m%d')

    trade_date_df = get_trade_cal(latest_trade_date, end_date)
    for row in trade_date_df.values.tolist():
        trade_date = row[0]
        if trade_date == latest_trade_date:
            continue
        print("Downloading", trade_date)
        ts_data = get_daily(trade_date)
        if ts_data is None:
            continue
        if ts_data.empty:
            continue
        column_mapping = {
            "trade_date_x": "tradedate",
            "high": "high",
            "low": "low",
            "open": "open",
            "close": "close",
            "adj_close": "adjclose",
            "vol": "volume",
            "amount": "amount",
            "ts_code": "symbol"
        }
        data = ts_data.rename(columns=column_mapping)[list(column_mapping.values())]      
        record_num = data.to_sql("ts_a_stock_eod_price", dbConnection, if_exists='append', index=False)
        print(f"{trade_date} Updated: {record_num} records")

if __name__ == '__main__':
    fire.Fire(dump_astock_data)
