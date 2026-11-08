"""Read public market data using the existing CI credential; never write Dolt."""

import datetime
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

import tushare as ts


def main():
    output = Path("source-diagnosis")
    output.mkdir(exist_ok=True)
    client = ts.pro_api(token=os.environ["TUSHARE"], timeout=25)
    results = []
    end = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
    requests = [("bse_mapping", {})]
    for symbol in ["920047.BJ", "920227.BJ", "920427.BJ", "838227.BJ"]:
        requests.append(("adj_factor", {"ts_code": symbol, "start_date": "20231001", "end_date": end}))
    for symbol in ["920047.BJ", "920227.BJ", "920427.BJ"]:
        requests.append(("daily", {"ts_code": symbol, "start_date": "20261020", "end_date": end}))
    for index in ["399300.SZ", "000905.SH", "000906.SH", "000852.SH"]:
        requests.append(("index_weight", {"index_code": index, "start_date": "20261001", "end_date": "20261031"}))
    for month in [6, 7, 8, 9, 10]:
        start = datetime.date(2026, month, 1)
        end_month = datetime.date(2026, month + 1, 1) - datetime.timedelta(days=1)
        requests.append(("index_weight", {"index_code": "000985.SH", "start_date": start.strftime("%Y%m%d"), "end_date": end_month.strftime("%Y%m%d")}))
    for number, (api, parameters) in enumerate(requests):
        label = f"{number:02d}-{api}"
        try:
            frame = getattr(client, api)(**parameters)
            records = json.loads(frame.to_json(orient="records"))
            (output / f"{label}.json").write_text(json.dumps({"api": api, "parameters": parameters, "rows": records}, indent=2))
            result = {"api": api, "parameters": parameters, "rows": len(frame), "file": f"{label}.json"}
            if "trade_date" in frame and not frame.empty:
                result["first_date"] = str(frame["trade_date"].min())
                result["last_date"] = str(frame["trade_date"].max())
            if api == "adj_factor" and not frame.empty:
                dates = ["20231013", "20231016", "20261026", "20261027", "20261106"]
                result["around_break"] = json.loads(frame.loc[frame["trade_date"].isin(dates)].sort_values("trade_date").to_json(orient="records"))
            if api == "index_weight" and not frame.empty:
                result["per_date"] = {str(date): {"count": int(len(group)), "weight_sum": float(group["weight"].sum())} for date, group in frame.groupby("trade_date")}
            if api == "bse_mapping":
                result["selected"] = json.loads(frame.loc[frame["n_code"].isin(["920047.BJ", "920227.BJ", "920427.BJ"])].to_json(orient="records"))
        except Exception as exc:
            # SDK transport errors may include request details; serialize only
            # their type. Successful artifacts contain public market fields.
            result = {"api": api, "parameters": parameters, "error_type": type(exc).__name__}
        results.append(result)
        print(json.dumps(result), flush=True)
        time.sleep(0.6)
    (output / "summary.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
