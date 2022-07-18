import tushare as ts
import os
import datetime
import pandas
import fire
import time

ts.set_token(os.environ["TUSHARE"])
pro=ts.pro_api()
file_path = os.path.dirname(os.path.realpath(__file__))


def get_trade_cal(start_date, end_date):
    df = pro.trade_cal(exchange='SSE', is_open='1',
                            start_date=start_date,
                            end_date=end_date,
                            fields='cal_date')
    return df

def get_daily(trade_date=''):
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

def dump_astock_data(start_date, end_date, skip_exists=True):
    trade_date_df = get_trade_cal(start_date, end_date)
    for row in trade_date_df.values.tolist():
        trade_date = row[0]
        filename = f'{file_path}/astock_daily/{trade_date}.csv'
        print(filename)
        if skip_exists and os.path.isfile(filename):
            continue
        data = get_daily(trade_date)
        if data is None:
            continue
        if data.empty:
            continue
        data.to_csv(filename, index=False)

if __name__ == '__main__':
    fire.Fire(dump_astock_data)
