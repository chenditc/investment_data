"""Align proven legacy-writer appends without changing published financial history."""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from ops.install_provider_scale_20261123 import Database
from ops.source_certificate import INDICES, verify

REPO = 'chenditc/investment_data'
AUDIT = 'ts_provider_reconciliation'
MAX_DAYS = 3
MIN_STOCKS = 1000
FIELDS = ('symbol','tradedate','open','high','low','close','adjclose','volume','amount')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def same(a,b):
    return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-12)


def latest_published(session=None):
    import requests
    session = session or requests.Session()
    headers={'Accept':'application/vnd.github+json'}
    token=os.environ.get('SOURCE_RECONCILIATION_TOKEN')
    if token:headers['Authorization']='Bearer '+token
    response = session.get(f'https://api.github.com/repos/{REPO}/releases/latest',headers=headers,timeout=30)
    response.raise_for_status();release=response.json()
    tag=release.get('tag_name','')
    if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',tag):
        raise ValueError('Invalid published release identity')
    assets={}
    for asset in release['assets']:
        if asset['name'] in assets:raise ValueError('Ambiguous release assets')
        assets[asset['name']]=asset
    if set(assets)!={'qlib_bin.tar.gz','qlib_bin.manifest.json'}:
        raise ValueError('Incomplete published release assets')
    manifest=assets['qlib_bin.manifest.json'];archive=assets['qlib_bin.tar.gz']
    if (any(a['state']!='uploaded' for a in assets.values())
            or not 0<manifest['size']<=16384
            or not re.fullmatch('sha256:[0-9a-f]{64}',manifest.get('digest') or '')):
        raise ValueError('Invalid published manifest asset')
    asset_id=manifest.get('id')
    if not isinstance(asset_id,int) or asset_id<=0:raise ValueError('Invalid manifest asset ID')
    url=f'https://api.github.com/repos/{REPO}/releases/assets/{asset_id}'
    response=session.get(url,headers={**headers,'Accept':'application/octet-stream'},timeout=30)
    response.raise_for_status();payload=response.content
    if len(payload)!=manifest['size'] or 'sha256:'+hashlib.sha256(payload).hexdigest()!=manifest['digest']:
        raise ValueError('Published manifest checksum mismatch')
    result=json.loads(payload)
    if (result.get('release_tag')!=tag
            or not re.fullmatch('[0-9a-v]{32}',result.get('dolt_commit',''))
            or not re.fullmatch('[0-9a-f]{40}',result.get('investment_data_commit',''))
            or not re.fullmatch('sha256:[0-9a-f]{64}',result.get('image_digest') or '')
            or result.get('archive_sha256')!=archive.get('digest')
            or result.get('archive_size_bytes')!=archive.get('size')):
        raise ValueError('Published archive/manifest identity mismatch')
    cutoff=result['target_trade_date']
    if datetime.date.fromisoformat(cutoff).isoformat()!=cutoff or cutoff>tag:
        raise ValueError('Invalid published trade date')
    return {'release_tag':tag,'date':cutoff,'commit':result['dolt_commit'],
            'manifest_sha256':manifest['digest']}


class Provider:
    def __init__(self,api=None):
        if api is None:
            import tushare as ts
            ts.set_token(os.environ['TUSHARE']);api=ts.pro_api()
        self.api=api;self.inputs=[];self.cache={}
    def get(self,api,**params):
        key=(api,tuple(sorted(params.items())))
        if key not in self.cache:
            frame=getattr(self.api,api)(**params)
            date=params['trade_date']
            if frame.empty or not frame['trade_date'].eq(date).all() or frame['ts_code'].duplicated().any():
                raise ValueError('Missing, duplicate or wrong-date provider input: '+api+' '+date)
            payload=frame.to_json(orient='records',double_precision=15).encode()
            self.inputs.append({'api':api,'params':params,'rows':len(frame),
                                'sha256':hashlib.sha256(payload).hexdigest()})
            self.cache[key]={row['ts_code']:row for row in json.loads(payload)}
        return self.cache[key]

    def calendar(self,start,end):
        params={'exchange':'SSE','is_open':'1','start_date':start.replace('-',''),
                'end_date':end.replace('-',''),'fields':'cal_date'}
        key=('trade_cal',tuple(sorted(params.items())))
        if key not in self.cache:
            frame=self.api.trade_cal(**params)
            if frame.empty or frame['cal_date'].duplicated().any():
                raise ValueError('Missing or duplicate provider trading calendar')
            dates=sorted(datetime.datetime.strptime(str(v),'%Y%m%d').date().isoformat() for v in frame['cal_date'])
            if any(not start<=date<=end for date in dates):raise ValueError('Wrong-range provider trading calendar')
            payload=frame.to_json(orient='records',double_precision=15).encode()
            self.inputs.append({'api':'trade_cal','params':params,'rows':len(frame),'sha256':hashlib.sha256(payload).hexdigest()})
            self.cache[key]=dates
        return self.cache[key]


def check_authority():
    if (os.environ.get('GITHUB_REF')!='refs/heads/main'
            or os.environ.get('GITHUB_EVENT_NAME') not in ('workflow_dispatch','schedule')):
        raise ValueError('Reconciliation writes require the verified main workflow')
    revision=Path('/opt/investment-data/REVISION').read_text().strip()
    if (revision!=os.environ.get('GITHUB_SHA')
            or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()!=revision):
        raise ValueError('Reconciliation image revision mismatch')


def check_history(db,published):
    base=published['commit'];cutoff=published['date']
    if db.query(f"SELECT DOLT_MERGE_BASE('HEAD','{base}') h")[0]['h']!=base:
        raise ValueError('Published source is not an available ancestor; restore sufficient Dolt history')
    published_date=db.query(f"SELECT MAX(tradedate) d FROM final_a_stock_eod_price AS OF '{base}'")[0]['d']
    if str(published_date)[:10]!=cutoff:raise ValueError('Published source date does not match its manifest')
    protected={'ts_a_stock_eod_price','final_a_stock_eod_price','ts_link_table',
               'ts_provider_factor_scale','ts_provider_factor_scale_manifest'}
    for row in db.query(f"SELECT * FROM dolt_schema_diff('{base}','WORKING')"):
        if (row.get('from_table_name') in protected or row.get('to_table_name') in protected
                or row.get('from_table_name')==AUDIT):
            raise ValueError('Financial source schema changed')
    for table in ('ts_provider_factor_scale','ts_provider_factor_scale_manifest'):
        if int(db.query(f"SELECT COUNT(*) n FROM dolt_diff('{base}','WORKING','{table}')")[0]['n']):
            raise ValueError('Registered factor metadata changed after publication')
    if int(db.query(f"SELECT COUNT(*) n FROM dolt_diff('{base}','WORKING','ts_link_table') WHERE diff_type<>'added'")[0]['n']):
        raise ValueError('Existing canonical link ratios changed')
    current_tables={next(iter(r.values())) for r in db.query('SHOW TABLES')}
    if AUDIT in current_tables and int(db.query(f"SELECT COUNT(*) n FROM dolt_diff('{base}','WORKING','{AUDIT}') WHERE diff_type<>'added'")[0]['n']):
        raise ValueError('Published reconciliation audit history changed')
    if int(db.query(f"SELECT COUNT(*) n FROM dolt_diff('{base}','WORKING','final_a_stock_eod_price') "
                f"WHERE diff_type<>'added' OR to_tradedate<='{cutoff}'")[0]['n']):
        raise ValueError('Published canonical price history changed')
    indices=','.join("'"+s+"'" for s in sorted(INDICES))
    nulls=' OR '.join('from_'+f+' IS NULL' for f in ('open','high','low','close','volume','amount'))
    seed="(from_tradedate='2004-12-31' AND from_close=1000 AND (from_open=0 OR from_high=0 OR from_low=0 OR from_volume=0 OR from_amount=0))"
    allowed=f"(diff_type='removed' AND from_symbol IN ({indices}) AND ({nulls} OR {seed}))"
    if int(db.query(f"SELECT COUNT(*) n FROM dolt_diff('{base}','WORKING','ts_a_stock_eod_price') "
                f"WHERE (diff_type<>'added' OR to_tradedate<='{cutoff}') AND NOT {allowed}")[0]['n']):
        raise ValueError('Published raw price history changed')


def bars(db,table,date,commit=None):
    version=f" AS OF '{commit}'" if commit else ''
    rows=db.query(f"SELECT {','.join(FIELDS)} FROM {table}{version} WHERE tradedate='{date}'")
    result={r['symbol']:r for r in rows}
    if len(rows)!=len(result):raise ValueError('Duplicate source quote')
    return result


def anchor(bar,factor,scale):
    value=float(factor)*float(scale);stored=float(bar['adjclose'])/float(bar['close'])
    if (not math.isfinite(value) or value<=0 or not math.isfinite(stored)
            or abs(stored-value)>abs(value)*.00001+.000001):
        raise ValueError('Trusted provider adjustment anchor changed: '+bar['symbol'])


def sql_round(db,values):
    if not values:return {}
    payload=json.dumps([{'symbol':r['symbol'],'old':float(r['old_raw']),'new':float(r['raw']),
                         'identity':float(r['identity']),'ratio':float(r['ratio'])} for r in values],
                       separators=(',',':'),allow_nan=False).replace("'","''")
    # JSON_TABLE uses the same DOUBLE operands as the price/link columns and
    # avoids thousands of literal UNION branches or OS argument-size limits.
    sql=("SELECT symbol,ROUND(old_raw/ratio,2) old_value,ROUND(new_raw/ratio,2) new_value,"
         "ROUND(identity_raw/ratio,2) legacy_value FROM JSON_TABLE('"+payload+"', '$[*]' COLUMNS("
         "symbol VARCHAR(16) PATH '$.symbol',old_raw DOUBLE PATH '$.old',new_raw DOUBLE PATH '$.new',"
         "identity_raw DOUBLE PATH '$.identity',ratio DOUBLE PATH '$.ratio')) v")
    with tempfile.TemporaryDirectory() as temporary:
        path=Path(temporary)/'round.sql';path.write_text(sql)
        rows=json.loads(db.run('sql','-r','json','--file',str(path))).get('rows',[])
    result={r['symbol']:(float(r['old_value']),float(r['new_value']),float(r['legacy_value'])) for r in rows}
    if len(result)!=len(values):raise ValueError('Incomplete SQL rounding result')
    return result


def make_plan(db,published,provider,now=None):
    if (not re.fullmatch('[0-9a-v]{32}',published.get('commit',''))
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',published.get('date',''))):
        raise ValueError('Invalid published source reference')
    check_history(db,published)
    now=now or datetime.datetime.now(ZoneInfo('Asia/Shanghai'))
    due=now.date() if now.hour>=17 else now.date()-datetime.timedelta(days=1)
    cutoff=published['date'];base=published['commit']
    if cutoff>due.isoformat():raise ValueError('Published baseline has a future trade date')
    dates=[str(r['d'])[:10] for r in db.query(
        f"SELECT DISTINCT tradedate d FROM final_a_stock_eod_price WHERE tradedate>'{cutoff}' ORDER BY tradedate LIMIT {MAX_DAYS+1}")]
    if len(dates)>MAX_DAYS:raise ValueError('Too many unpublished days for automatic reconciliation')
    if any(datetime.date.fromisoformat(d).isoformat()!=d or d>due.isoformat() for d in dates):
        raise ValueError('Unpublished source contains a future or not-due date')
    if not dates:return {'published':published,'changes':[],'days':[],'inputs':[]}
    if provider.calendar(cutoff,dates[-1])!=[cutoff]+dates:
        raise ValueError('Missing unpublished trading day between the release and source')
    metadata=db.query('SELECT symbol,scale,effective_date,reference_date,reference_close,reference_adjclose FROM ts_provider_factor_scale')
    scales={r['symbol']:r for r in metadata}
    if not scales or len(scales)!=len(metadata):raise ValueError('Invalid registered factor cohort')
    for row in metadata:
        value=float(row['scale'])
        if (not re.fullmatch(r'[0-9]{6}\.(SZ|SH|BJ)',row['symbol'])
                or not math.isfinite(value) or value<=0
                or str(row['effective_date'])[:10]>cutoff):
            raise ValueError('Invalid registered factor conversion')
    trusted=bars(db,'ts_a_stock_eod_price',cutoff,base)
    previous=provider.get('adj_factor',trade_date=cutoff.replace('-',''))
    checked=set()
    for symbol,bar in trusted.items():
        if symbol in INDICES:continue
        if symbol not in previous:raise ValueError('Published stock anchor missing from provider')
        anchor(bar,previous[symbol]['adj_factor'],scales.get(symbol,{}).get('scale',1))
        checked.add(symbol)
    links={}
    for row in db.query('SELECT link_symbol,w_symbol,adj_ratio FROM ts_link_table'):
        links.setdefault(row['link_symbol'],[]).append(row)
    changes=[];days=[];before={}
    for date in dates:
        raw=bars(db,'ts_a_stock_eod_price',date);final=bars(db,'final_a_stock_eod_price',date)
        quotes=provider.get('daily',trade_date=date.replace('-',''))
        factors=provider.get('adj_factor',trade_date=date.replace('-',''))
        stocks={s for s in raw if s not in INDICES}
        if len(stocks)<=MIN_STOCKS or stocks!=set(quotes):
            raise ValueError('Unpublished stock cohort differs from complete provider quotes')
        if len(raw)!=len(final):raise ValueError('Unpublished raw/canonical cohorts differ')
        values=[];seen=set()
        for symbol,bar in sorted(raw.items()):
            if not re.fullmatch(r'[0-9]{6}\.(SZ|SH|BJ)',symbol) or len(links.get(symbol,[]))!=1:
                raise ValueError('Missing, duplicate or invalid canonical link')
            link=links[symbol][0];canonical=link['w_symbol'];ratio=float(link['adj_ratio'])
            if (not re.fullmatch(r'(SZ|SH|BJ)[0-9]{6}',canonical)
                    or not math.isfinite(ratio) or ratio<=0 or canonical not in final or canonical in seen):
                raise ValueError('Invalid canonical identity or link ratio')
            seen.add(canonical)
            for field in ('open','high','low','close','volume','amount'):
                value=float(bar[field])
                if not math.isfinite(value) or value<=0 or not same(value,final[canonical][field]):
                    raise ValueError('Unpublished raw/canonical quote mismatch: '+symbol+' '+field)
                if symbol not in INDICES:
                    expected=float(quotes[symbol]['vol' if field=='volume' else field])
                    if not math.isfinite(expected) or not math.isclose(value,expected,rel_tol=1e-10,abs_tol=1e-6):
                        raise ValueError('Unpublished quote differs from provider: '+symbol+' '+field)
            old=float(bar['adjclose']);new=old;identity=old;scale=1.0;factor=None
            if not math.isfinite(old) or old<=0:raise ValueError('Invalid source adjusted close')
            if symbol not in INDICES:
                if symbol not in factors:raise ValueError('Missing current provider factor')
                factor=float(factors[symbol]['adj_factor'])
                if not math.isfinite(factor) or factor<=0:raise ValueError('Invalid current provider factor')
                scale=float(scales.get(symbol,{}).get('scale',1))
                if symbol not in checked:
                    if symbol in scales:
                        reference=scales[symbol];refdate=str(reference['reference_date'])[:10]
                        ref=bars(db,'ts_a_stock_eod_price',refdate,base).get(symbol)
                        if (ref is None or not same(ref['close'],reference['reference_close'])
                                or not same(ref['adjclose'],reference['reference_adjclose'])):
                            raise ValueError('Registered historical reference changed')
                        ref_factor=provider.get('adj_factor',trade_date=refdate.replace('-','')).get(symbol)
                        if ref_factor is None:raise ValueError('Missing resumed-stock reference')
                        anchor(ref,ref_factor['adj_factor'],scale)
                    else:
                        history=db.query(f"SELECT symbol,tradedate,close,adjclose FROM ts_a_stock_eod_price AS OF '{base}' "
                                         f"WHERE symbol='{symbol}' ORDER BY tradedate DESC LIMIT 1")
                        if history:
                            ref=history[0];refdate=str(ref['tradedate'])[:10]
                            ref_factor=provider.get('adj_factor',ts_code=symbol,trade_date=refdate.replace('-','')).get(symbol)
                            if ref_factor is None:raise ValueError('Missing unregistered historical anchor')
                            anchor(ref,ref_factor['adj_factor'],1)
                        # No history: a genuine new series can start on the identity basis.
                    checked.add(symbol)
                identity=float(bar['close'])*factor;aligned=identity*scale
                if same(old,aligned):pass
                elif same(old,identity):new=old*scale
                else:
                    # Retain a previously aligned value within the original rounding tolerance.
                    anchor(bar,factor,scale)
            if not math.isfinite(new) or new<=0:raise ValueError('Invalid aligned raw adjustment')
            values.append({'symbol':symbol,'canonical':canonical,'date':date,'ratio':ratio,
                           'scale':scale,'provider_factor':factor,'identity':identity,'old_raw':old,'raw':new,
                           'old_final':float(final[canonical]['adjclose'])})
        if seen!=set(final):raise ValueError('Unexpected canonical security')
        rounded=sql_round(db,values)
        day_changes=[]
        for row in values:
            old_expected,new_expected,legacy_expected=rounded[row['symbol']]
            if not math.isfinite(new_expected) or new_expected<=0:
                raise ValueError('Invalid reconciled canonical adjustment')
            if not any(same(row['old_final'],v) for v in (old_expected,new_expected,legacy_expected)):
                raise ValueError('Canonical adjustment has an unrelated discrepancy')
            row['final']=new_expected
            if not same(row['old_raw'],row['raw']) or not same(row['old_final'],row['final']):
                changes.append(row);day_changes.append(row)
        days.append({'date':date,'stock_rows':len(stocks),'total_rows':len(raw),
                     'raw_cells':sum(not same(r['old_raw'],r['raw']) for r in day_changes),
                     'canonical_cells':sum(not same(r['old_final'],r['final']) for r in day_changes)})
        before[date]={'raw':raw,'canonical':final}
    return {'published':published,'source_base':db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h'],
            'days':days,'changes':changes,'inputs':provider.inputs,'before':before}


def check_result(db,plan):
    expected={(r['date'],r['symbol']):r for r in plan['changes']}
    canonical={(r['date'],r['canonical']):r for r in plan['changes']}
    for date,old_tables in plan['before'].items():
        for name,table in [('raw','ts_a_stock_eod_price'),('canonical','final_a_stock_eod_price')]:
            current=bars(db,table,date);old=old_tables[name]
            if current.keys()!=old.keys():raise ValueError('Reconciliation changed the quote cohort')
            for symbol,row in old.items():
                for field in FIELDS:
                    change=(expected if name=='raw' else canonical).get((date,symbol))
                    value=change['raw' if name=='raw' else 'final'] if change and field=='adjclose' else row[field]
                    actual=current[symbol][field]
                    if (field in ('symbol','tradedate') and str(actual)!=str(value)) or (
                            field not in ('symbol','tradedate') and not same(actual,value)):
                        raise ValueError('Unexpected field change during reconciliation')
    check_history(db,plan['published'])


def reconcile(db,published=None,provider=None,now=None,authority=check_authority):
    refresh_publication=published is None
    if published is None:
        try:
            if verify(db)['result']=='verified':return {'result':'verified_snapshot','days':[]}
        except ValueError:pass
        published=latest_published()
    plan=make_plan(db,published,provider or Provider(),now)
    if not plan['changes']:return {'result':'unchanged','days':plan['days']}
    authority()
    if refresh_publication and latest_published()!=published:
        raise ValueError('Published release changed during reconciliation; retry from its new baseline')
    audit={k:v for k,v in plan.items() if k!='before'};plan_hash=digest(audit)
    db.run('sql','-q',f'''CREATE TABLE IF NOT EXISTS {AUDIT} (
      event_id VARCHAR(64) PRIMARY KEY,source_base VARCHAR(32) NOT NULL,
      published_commit VARCHAR(32) NOT NULL,plan_sha256 VARCHAR(64) NOT NULL,report_json LONGTEXT NOT NULL)''')
    event=plan['source_base']+'-'+plan['days'][-1]['date']
    if db.query(f"SELECT event_id FROM {AUDIT} WHERE event_id='{event}'"):
        raise ValueError('Conflicting reconciliation event already exists')
    statements=['START TRANSACTION;']
    for row in plan['changes']:
        for table,symbol,key,old_key in [('ts_a_stock_eod_price',row['symbol'],'raw','old_raw'),
                                        ('final_a_stock_eod_price',row['canonical'],'final','old_final')]:
            if not same(row[key],row[old_key]):
                statements.append(f"UPDATE {table} SET adjclose={row[key]!r} WHERE symbol='{symbol}' AND tradedate='{row['date']}';")
    payload=json.dumps(audit,sort_keys=True,separators=(',',':')).replace("'","''")
    statements.append(f"INSERT INTO {AUDIT} VALUES ('{event}','{plan['source_base']}','{published['commit']}','{plan_hash}','{payload}');")
    statements.append('COMMIT;')
    with tempfile.TemporaryDirectory() as temporary:
        path=Path(temporary)/'reconcile.sql';path.write_text('\n'.join(statements));db.run('sql','--file',str(path))
    check_result(db,plan)
    return {'result':'reconciled','published':published,'source_base':plan['source_base'],
            'plan_sha256':plan_hash,'days':plan['days'],'provider_inputs':plan['inputs']}


def main():
    if len(sys.argv)!=1:raise SystemExit('usage: python3 -m ops.reconcile_unpublished')
    result=reconcile(Database('/dolt/investment_data'))
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':main()
