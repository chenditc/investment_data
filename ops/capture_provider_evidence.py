"""Read-only provider snapshots for the Nov23 factor-anchor incident."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import tushare as ts


def main():
    ts.set_token(os.environ["TUSHARE"])
    pro = ts.pro_api()
    output = Path("/evidence")
    output.mkdir(exist_ok=True, parents=True)
    manifest = []
    for date in ("20261120", "20261123"):
        for api in ("adj_factor", "daily"):
            frame = getattr(pro, api)(trade_date=date)
            if frame.empty or not frame["trade_date"].eq(date).all():
                raise ValueError("Missing or wrong-date provider evidence: " + api + " " + date)
            data = frame.to_json(orient="records", double_precision=15).encode()
            name = api + "-" + date + ".json"
            (output / name).write_bytes(data)
            manifest.append({"api": api, "trade_date": date, "file": name, "rows": len(frame),
                             "sha256": hashlib.sha256(data).hexdigest(),
                             "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
