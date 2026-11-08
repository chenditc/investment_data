import json, os, time
from pathlib import Path
import tushare as ts
root=Path('source-diagnosis');root.mkdir(exist_ok=True)
pro=ts.pro_api(os.environ['TUSHARE'],timeout=30)
rows=[];summary=[]
for canonical,provider,months,expected in [('399300.SZ','399300.SZ',[10],300),('000905.SH','000905.SH',[10],500),('000906.SH','000906.SH',[10],800),('000852.SH','000852.SH',[10],1000),('000985.SH','000985.CSI',[6,7,8,9,10],None)]:
    for month in months:
        import calendar
        start=f'2026{month:02d}01';end=f'2026{month:02d}{calendar.monthrange(2026,month)[1]}'
        frame=pro.index_weight(index_code=provider,start_date=start,end_date=end)
        if frame.empty or (expected is not None and len(frame)!=expected):
            raise RuntimeError(f'Incomplete monthly index snapshot: {canonical} {month}')
        if frame['con_code'].duplicated().any() or frame['trade_date'].nunique()!=1 or not frame['index_code'].eq(provider).all():
            raise RuntimeError('Invalid index snapshot')
        total=float(frame['weight'].sum())
        if abs(total-100)>.5:raise RuntimeError('Incomplete weight total')
        group=[{'index_code':canonical,'stock_code':r.con_code,'trade_date':r.trade_date,'weight':r.weight} for r in frame.itertuples()]
        rows.extend(group)
        item={'canonical':canonical,'provider':provider,'rows':len(frame),'date':str(frame['trade_date'].iloc[0]),'weight_sum':total}
        summary.append(item);print(json.dumps(item),flush=True);time.sleep(1)
(root/'index-repair-rows.json').write_text(json.dumps(rows))
(root/'index-repair-summary.json').write_text(json.dumps(summary,indent=2))
