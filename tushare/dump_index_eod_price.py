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
    df = df.sort_values(by="cal_date").reset_index(drop=True)
    return df

index_list = ['399300.SZ', '000905.SH', '000300.SH', '000906.SH', '000852.SH', '000985.SH']

def dump_index_data(start_date="19900101", end_date="20500101", skip_exists=True):
    trade_date_df = get_trade_cal(start_date, end_date)

    if not os.path.exists(f"{file_path}/index/"):
        os.makedirs(f"{file_path}/index/")
    
    for index_name in index_list:
        print(f"Processing {index_name}")
        filename = f'{file_path}/index/{index_name}.csv'
        result_df_list = []
        for time_slice in range(int(len(trade_date_df)/4000) + 1):
            start_date = trade_date_df["cal_date"][time_slice * 4000]
            end_index = min((time_slice+1) * 4000 - 1, len(trade_date_df) - 1)
            end_date = trade_date_df["cal_date"][end_index]
            df = pro.index_daily(ts_code=index_name, start_date = start_date, end_date=end_date)
            if df.empty:
                continue
            result_df_list.append(df)
        if len(result_df_list) == 0:
            continue
        result_df = pandas.concat(result_df_list)
        result_df["tradedate"] = result_df["trade_date"]
        result_df["volume"] = result_df["vol"]
        result_df["symbol"] = result_df["ts_code"]
        result_df["adjclose"] = result_df["close"]
        result_df.to_csv(filename, index=False)

if __name__ == '__main__':
    fire.Fire(dump_index_data)
