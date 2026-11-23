import tushare as ts
import os
import datetime
import pandas
import fire
import time
import math
import json
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine
import pymysql

ts.set_token(os.environ["TUSHARE"])
pro=ts.pro_api()

def load_factor_scales(connection):
    """Use only explicitly installed, audited provider-basis conversions."""
    present = pandas.read_sql(
        "SELECT COUNT(*) AS n FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'ts_provider_factor_scale'",
        connection,
    )
    if int(present['n'].iloc[0]) == 0:
        return {}
    rows = pandas.read_sql(
        'SELECT symbol, scale, effective_date FROM ts_provider_factor_scale', connection,
    )
    scales = {}
    for row in rows.itertuples(index=False):
        scale = float(row.scale)
        if pandas.isna(row.effective_date):
            raise RuntimeError('Invalid registered provider factor date')
        date = pandas.Timestamp(row.effective_date).strftime('%Y%m%d')
        if row.symbol in scales or not math.isfinite(scale) or scale <= 0:
            raise RuntimeError('Invalid registered provider factor scale')
        scales[row.symbol] = (scale, date)
    if not scales:
        raise RuntimeError('Registered provider factor scale table is empty')
    return scales


def align_factor_basis(fresh, trade_date, factor_scales):
    fresh = fresh.copy()
    if factor_scales:
        def scale_for(symbol):
            scale, effective = factor_scales.get(symbol, (1.0, '00000000'))
            return scale if trade_date >= effective else 1.0
        fresh['adj_factor'] = fresh['adj_factor'] * fresh['ts_code'].map(scale_for)
    return fresh


def verify_unmapped_anchors(connection, frame, factor_scales):
    """An unregistered existing security must still pass the strict identity check."""
    if not factor_scales:
        return
    for symbol in sorted(set(frame['symbol']) - set(factor_scales)):
        stored = pandas.read_sql(
            'SELECT symbol,tradedate,close,adjclose FROM ts_a_stock_eod_price '
            'WHERE symbol = %s ORDER BY tradedate DESC LIMIT 1',
            connection, params=(symbol,),
        )
        if stored.empty:
            continue  # A new listing has no earlier factor basis to preserve.
        row = stored.iloc[0]
        date = pandas.Timestamp(row['tradedate']).strftime('%Y%m%d')
        fresh = pro.adj_factor(ts_code=symbol, trade_date=date)
        if (len(fresh) != 1 or fresh['ts_code'].iloc[0] != symbol
                or fresh['trade_date'].iloc[0] != date):
            raise RuntimeError(f'Missing adjustment anchor for unregistered security {symbol}')
        value = float(fresh['adj_factor'].iloc[0])
        if not math.isfinite(float(row['close'])) or float(row['close']) <= 0:
            raise RuntimeError(f'Invalid stored adjustment anchor for {symbol}')
        old = float(row['adjclose']) / float(row['close'])
        if (not math.isfinite(value) or value <= 0 or not math.isfinite(old)
                or abs(old - value) > abs(value) * .00001 + .000001):
            raise RuntimeError(f'Unregistered provider factor basis for {symbol}; verify and register its scale')


def verify_adjustment_anchor(connection, trade_date, factor_scales=None):
    """Detect provider restatements before mixing factor scales across days."""
    canonical = datetime.datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d')
    stored = pandas.read_sql(
        f"SELECT symbol, close, adjclose FROM ts_a_stock_eod_price WHERE tradedate = '{canonical}' AND close > 0",
        connection,
    )
    fresh = pro.adj_factor(trade_date=trade_date)
    if fresh.empty or not fresh['trade_date'].eq(trade_date).all():
        raise RuntimeError(f'Missing adjustment anchor for {trade_date}')
    fresh = align_factor_basis(fresh, trade_date, factor_scales)
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
        details = [
            {'symbol': merged.loc[i, 'symbol'], 'stored_factor': float(old_factor.loc[i]),
             'aligned_provider_factor': float(merged.loc[i, 'adj_factor'])}
            for i in merged.index[changed][:10]
        ]
        raise RuntimeError(
            f'Provider adjustment history changed for {trade_date}: {symbols}; '
            'refresh the affected history before appending another trading day; '
            f'{int(changed.sum())} mismatches; details={json.dumps(details)}'
        )

def get_trade_cal(start_date, end_date):
    df = pro.trade_cal(exchange='SSE', is_open='1',
                            start_date=start_date,
                            end_date=end_date,
                            fields='cal_date')
    return df

def get_daily(trade_date, factor_scales=None):
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
            adj_factor = align_factor_basis(adj_factor, trade_date, factor_scales)
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
        factor_scales = load_factor_scales(dbConnection)
        verify_adjustment_anchor(dbConnection, latest_trade_date, factor_scales)
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
        ts_data = get_daily(trade_date, factor_scales=factor_scales) if factor_scales else get_daily(trade_date)
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
        if factor_scales:
            check_connection = sqlEngine.raw_connection()
            try:
                verify_unmapped_anchors(check_connection, data, factor_scales)
            finally:
                check_connection.close()
        record_num = data.to_sql("ts_a_stock_eod_price", sqlEngine, if_exists='append', index=False)
        print(f"{trade_date} Updated: {record_num} records")

if __name__ == '__main__':
    fire.Fire(dump_astock_data)
