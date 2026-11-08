import tushare as ts
import os
import datetime
import pandas
import fire
import time
from pathlib import Path
from zoneinfo import ZoneInfo

ts.set_token(os.environ["TUSHARE"])
pro=ts.pro_api()
file_path = os.path.dirname(os.path.realpath(__file__))

# Keep this list in sync with the start-date query in daily_update.sh.
index_list = [
    '000905.SH', # csi500
    '399300.SZ', # csi300
    '000906.SH', # csi800
    '000852.SH', # csi1000
    '000985.SH', # csiall
    ]
PROVIDER_INDEX_CODES = {'000985.SH': '000985.CSI'}

def _date(value):
    text = str(value)
    parsed = datetime.datetime.strptime(text, "%Y%m%d").date()
    if parsed.strftime("%Y%m%d") != text:
        raise ValueError("Index collection dates must be YYYYMMDD")
    return parsed


def _windows(start, end):
    while start <= end:
        # Tushare bounds are inclusive. Include the final partial window and
        # move to the following day without overlapping adjacent requests.
        stop = min(start + datetime.timedelta(days=14), end)
        yield start.strftime("%Y%m%d"), stop.strftime("%Y%m%d")
        start = stop + datetime.timedelta(days=1)


def dump_index_data(start_date=None, end_date=None, skip_exists=True):
    # Keep the existing CLI argument for compatibility. Incremental downloads
    # replace each invocation's output rather than reusing an older CSV.
    output_dir = Path(file_path) / "index_weight"
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date()
    end = min(_date(end_date), today) if end_date is not None else today

    for index_name in index_list:
        provider_code = PROVIDER_INDEX_CODES.get(index_name, index_name)
        filename = output_dir / f"{index_name}.csv"
        filename.unlink(missing_ok=True)
        if start_date is None:
            index_info = pro.index_basic(ts_code=index_name)
            start = _date(index_info["list_date"].iloc[0])
        else:
            start = _date(start_date)
        result_frames = []
        for first, last in _windows(start, end):
            df = pro.index_weight(
                index_code=provider_code, start_date=first, end_date=last,
            )
            time.sleep(0.5)
            if df.empty:
                print(f"Empty index data: {index_name} {first}..{last}")
                continue
            required = ["index_code", "con_code", "trade_date", "weight"]
            if any(column not in df for column in required):
                raise ValueError(f"Incomplete index weight response for {index_name}")
            df = df[required].copy()
            dates = df["trade_date"].astype(str)
            if not df["index_code"].eq(provider_code).all() \
                    or not dates.between(first, last).all():
                raise ValueError(f"Unexpected index or date in response for {index_name}")
            if df[required].isna().any().any():
                raise ValueError(f"Null index weight fields for {index_name}")
            df['index_code'] = index_name
            result_frames.append(df)
        if not result_frames:
            print(f"No index snapshots returned: {index_name} {start}..{end}")
            continue
        result = pandas.concat(result_frames, ignore_index=True).drop_duplicates()
        if result.duplicated(["index_code", "con_code", "trade_date"]).any():
            raise ValueError(f"Conflicting index weights for {index_name}")
        result = result.rename(columns={"con_code": "stock_code"})
        result = result[["index_code", "stock_code", "trade_date", "weight"]]
        result = result.sort_values(["trade_date", "stock_code"])
        result.to_csv(filename, index=False)
        print(f"Exported {len(result)} index weight rows for {index_name}; latest={result['trade_date'].max()}")

if __name__ == '__main__':
    fire.Fire(dump_index_data)
