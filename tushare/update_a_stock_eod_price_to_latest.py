import tushare as ts
import os
import datetime
import pandas
import fire
import time
import math
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
import pymysql

ts.set_token(os.environ["TUSHARE"])
pro=ts.pro_api()

def verify_adjustment_anchor(connection, trade_date):
    """Detect provider restatements before mixing factor scales across days."""
    canonical = datetime.datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d')
    stored = pandas.read_sql(
        f"SELECT symbol, close, adjclose FROM ts_a_stock_eod_price WHERE tradedate = '{canonical}' AND close > 0",
        connection,
    )
    fresh = pro.adj_factor(trade_date=trade_date)
    if fresh.empty or not fresh['trade_date'].eq(trade_date).all():
        raise RuntimeError(f'Missing adjustment anchor for {trade_date}')
    indices=['399300.SZ','000905.SH','000300.SH','000906.SH','000852.SH','000985.SH']
    stored=stored.loc[~stored['symbol'].isin(indices)]
    merged = pandas.merge(stored, fresh, left_on='symbol', right_on='ts_code', how='left', validate='one_to_one')
    if merged.empty or merged[['adj_factor','adjclose','close']].isna().any().any() \
            or not merged['adj_factor'].gt(0).all() \
            or not all(merged[column].map(math.isfinite).all() for column in ['adj_factor','adjclose','close']):
        raise RuntimeError(f'Invalid adjustment anchor for {trade_date}')
    old_factor = merged['adjclose'] / merged['close']
    changed = (old_factor - merged['adj_factor']).abs() > merged['adj_factor'].abs() * 0.00001 + 0.000001
    if changed.any():
        symbols = ', '.join(merged.loc[changed, 'symbol'].head(10))
        raise RuntimeError(
            f'Provider adjustment history changed for {trade_date}: {symbols}; '
            'refresh the affected history before appending another trading day'
        )

def get_trade_cal(start_date, end_date):
    df = pro.trade_cal(exchange='SSE', is_open='1',
                            start_date=start_date,
                            end_date=end_date,
                            fields='cal_date')
    return df

def get_daily(trade_date):
    for attempt in range(3):
        try:
            price_df = pro.daily(trade_date=trade_date)
            if price_df.empty:
                return price_df
            if not price_df["trade_date"].eq(trade_date).all():
                raise ValueError(f"Unexpected price date for {trade_date}")
            price_fields = ["open", "high", "low", "close", "vol", "amount"]
            if price_df[price_fields].isna().any().any():
                raise ValueError(f"Incomplete daily prices for {trade_date}")
            adj_factor = pro.adj_factor(trade_date=trade_date)
            df = pandas.merge(
                price_df, adj_factor, on=["ts_code", "trade_date"],
                how="left", validate="one_to_one",
            )
            if df["adj_factor"].isna().any() or not df["adj_factor"].gt(0).all():
                raise ValueError(f"Missing or invalid adjustment factors for {trade_date}")
            df["adj_close"] = df["close"] * df["adj_factor"]
            return df
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(
                    f"Failed to download complete daily data for {trade_date} after 3 attempts"
                ) from e
            print(f"Retrying daily data for {trade_date}: {e}")
            time.sleep(1)

def dump_astock_data():
    sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
    dbConnection = sqlEngine.raw_connection()

    sql = """
    select max(tradedate) as tradedate
        FROM
        (select tradedate, count(tradedate) as symbol_count 
        FROM ts_a_stock_eod_price 
        where tradedate > "2023-05-01" 
        group by tradedate) tradedate_record
    WHERE symbol_count > 1000
    """
    try:
        latest_trade_date = pandas.read_sql(sql, dbConnection)["tradedate"][0].strftime('%Y%m%d')
        verify_adjustment_anchor(dbConnection, latest_trade_date)
    finally:
        dbConnection.close()
    now = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    end_date = now.strftime('%Y%m%d')

    trade_date_df = get_trade_cal(latest_trade_date, end_date)
    if latest_trade_date not in trade_date_df["cal_date"].tolist():
        raise RuntimeError("Trading calendar is missing the last stored trading day")
    # Sort from small to big, so that we process earlier date first
    trade_date_df = trade_date_df.sort_values("cal_date")
    for row in trade_date_df.values.tolist():
        trade_date = row[0]
        if trade_date == latest_trade_date:
            continue
        # Tushare publishes daily prices at 15:00-16:00 China time. Wait until
        # 17:00 to avoid treating an intraday or partial response as a full day.
        if trade_date == end_date and now.hour < 17:
            print(f"Daily data for {trade_date} is not due until 17:00 Asia/Shanghai")
            break
        print("Downloading", trade_date)
        ts_data = get_daily(trade_date)
        if ts_data is None or ts_data.empty:
            raise RuntimeError(f"No daily data returned for expected trading day {trade_date}")
        column_mapping = {
            "trade_date": "tradedate",
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
        record_num = data.to_sql("ts_a_stock_eod_price", sqlEngine, if_exists='append', index=False)
        print(f"{trade_date} Updated: {record_num} records")

if __name__ == '__main__':
    fire.Fire(dump_astock_data)
