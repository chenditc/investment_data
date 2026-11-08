"""Collect reproducible public inputs for a Dolt repair, without database writes."""
import datetime
import json
import os
from pathlib import Path
import time
import tushare as ts

OUT = Path('source-diagnosis')
OUT.mkdir(exist_ok=True)
client = ts.pro_api(os.environ['TUSHARE'], timeout=30)

def call(api, **parameters):
    for attempt in range(3):
        try:
            result = getattr(client, api)(**parameters)
            time.sleep(.6)
            return json.loads(result.to_json(orient='records'))
        except Exception:
            if attempt == 2:
                raise RuntimeError(f'{api} failed after retries') from None
            time.sleep(2)

mapping = call('bse_mapping')
(OUT/'mapping.json').write_text(json.dumps(mapping))
aliases = {r['o_code']:r['n_code'] for r in mapping}
listing_dates = {r['n_code']:r['list_date'] for r in mapping}
manifest = json.loads(Path('ops/source_repair_candidates.json').read_text())
targets = {}
for item in manifest['candidates']:
    symbol = item['symbol']
    provider = symbol[2:] + '.' + symbol[:2]
    provider = aliases.get(provider, provider)
    targets[provider] = min(targets.get(provider,'99999999'), item['start_date'], listing_dates.get(provider,'99999999'))
results = []
for provider, start in sorted(targets.items()):
    rows = []
    for first,last in [('20000104','20091231'),('20100101','20191231'),('20200101','20261106')]:
        if start > last:
            continue
        first = max(first,start)
        rows.extend(call('adj_factor',ts_code=provider,start_date=first,end_date=last))
    dates = [r['trade_date'] for r in rows]
    if len(dates) != len(set(dates)) or any(r['ts_code'] != provider for r in rows):
        raise RuntimeError(f'Invalid factor response for {provider}')
    name = provider.replace('.','_')+'.json'
    (OUT/name).write_text(json.dumps(sorted(rows,key=lambda r:r['trade_date'])))
    item = {'provider':provider,'rows':len(rows),'first':min(dates) if dates else None,'last':max(dates) if dates else None,'file':name}
    results.append(item)
    print(json.dumps(item),flush=True)
probes=[]
for parameters in [
    {'index_code':'000985.SH','start_date':'20260501','end_date':'20260531'},
    {'index_code':'000985.SH','start_date':'20261001','end_date':'20261031','limit':10000,'offset':0},
    {'index_code':'000985.CSI','start_date':'20261001','end_date':'20261031'},
    {'index_code':'399985.SZ','start_date':'20261001','end_date':'20261031'},
]:
    try:
        rows=call('index_weight',**parameters)
        item={'parameters':parameters,'rows':len(rows),'dates':sorted({r['trade_date'] for r in rows})}
        if rows:
            name=f'index-probe-{len(probes)}.json'
            (OUT/name).write_text(json.dumps(rows));item['file']=name
    except RuntimeError:
        item={'parameters':parameters,'error':'request_failed'}
    probes.append(item);print(json.dumps(item),flush=True)
(OUT/'factor-summary.json').write_text(json.dumps(results,indent=2))
(OUT/'index-probes.json').write_text(json.dumps(probes,indent=2))
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2))
