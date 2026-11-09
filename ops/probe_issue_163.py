"""Fetch authoritative BSE daily bars and factors; never write the database."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import tushare as ts

out = Path('/output')
out.mkdir(exist_ok=True)
client = ts.pro_api(token=os.environ['TUSHARE'], timeout=25)
manifest = []
for date in ['20251030', '20251031', '20251103']:
    for api in ['daily', 'adj_factor']:
        for attempt in range(3):
            try:
                frame = getattr(client, api)(trade_date=date)
                if frame.empty or not frame['trade_date'].eq(date).all():
                    raise ValueError('Missing or unexpected provider date')
                frame = frame.loc[frame['ts_code'].str.endswith('.BJ')].sort_values('ts_code')
                if len(frame) < 270 or frame['ts_code'].duplicated().any():
                    raise ValueError('Incomplete or duplicate BSE response')
                if api == 'adj_factor' and not frame['adj_factor'].map(lambda v: math.isfinite(v) and v > 0).all():
                    raise ValueError('Invalid factor')
                rows = json.loads(frame.to_json(orient='records', double_precision=15))
                path = out / f'{api}-{date}.json'
                path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
                entry = {'api':api, 'parameters':{'trade_date':date}, 'filter':'ts_code suffix .BJ',
                         'rows':len(rows), 'file':path.name, 'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                manifest.append(entry)
                print(json.dumps(entry), flush=True)
                break
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f'{api} {date}: {type(exc).__name__}') from None
                time.sleep(1)
        time.sleep(.5)
(out / 'manifest.json').write_text(json.dumps(manifest, indent=2))
