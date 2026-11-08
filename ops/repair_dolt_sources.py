"""Build and apply a pinned, auditable correction to source adjustment prices."""
import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

BASE = 'kl67hfmqtpko9f0kjnu0hqdutjbvvkqs'

def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite repair input')
    return result

def rounded(value):
    return float(Decimal(str(value)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP))

def provider_symbol(symbol):
    return symbol[2:]+'.'+symbol[:2]

def write_csv(path,columns,rows):
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader();writer.writerows(rows)

class Database:
    def __init__(self,path):
        self.path=Path(path)
        self.binary=os.environ.get('DOLT_BIN','dolt')
    def run(self,*args):
        return subprocess.check_output([self.binary,*args],cwd=self.path,text=True)
    def query(self,sql):
        return json.loads(self.run('sql','-r','json','-q',sql))['rows']
    def check_base(self):
        if self.query("SELECT DOLT_HASHOF('HEAD') AS value")[0]['value'] != BASE:
            raise ValueError('Repair base commit changed')
        if 'nothing to commit, working tree clean' not in self.run('status'):
            raise ValueError('Repair checkout is dirty')

def build_rows(raw,final,links,wind,factors,aliases):
    """Change only adjustment prices, calibrating legacy stocks at their anchor."""
    final_by_key={(r['symbol'],r['tradedate']):r for r in final}
    raw_by_key={(r['symbol'],r['tradedate']):r for r in raw}
    wind_by_symbol={}
    for row in wind:
        wind_by_symbol.setdefault(row['symbol'],[]).append(row)
    scales={};link_rows=[]
    for link in links:
        source=link['link_symbol'];symbol=link['w_symbol']
        provider=aliases.get(source,source)
        if provider not in factors:
            raise ValueError(f'Missing factor history for {source}')
        if symbol.startswith('BJ'):
            scale=1.0
        else:
            anchor=link['link_date']
            old=final_by_key.get((symbol,anchor))
            source_row=raw_by_key.get((source,anchor))
            if not old or not source_row or anchor not in factors[provider]:
                raise ValueError(f'Missing alignment anchor for {source} at {anchor}')
            canonical=number(old['adjclose'])
            reference=[r for r in wind_by_symbol.get(symbol,[]) if r['tradedate']==anchor]
            if reference:
                canonical=number(reference[0]['adjclose'])
            scale=number(source_row['close'])*factors[provider][anchor]/canonical
            # Multiple reference dates must agree with the chosen scale.
            checks=[]
            prior=[r for r in wind_by_symbol.get(symbol,[]) if r['tradedate']<=anchor][-5:]
            for row in prior:
                candidate=raw_by_key.get((source,row['tradedate']))
                factor=factors[provider].get(row['tradedate'])
                if candidate and factor and number(row['adjclose'])>0:
                    checks.append(number(candidate['close'])*factor/number(row['adjclose']))
            if any(abs(value/scale-1)>.005 for value in checks):
                raise ValueError(f'Inconsistent canonical overlap for {source}')
        if not math.isfinite(scale) or scale<=0:
            raise ValueError(f'Invalid alignment scale for {source}')
        scales[source]=scale
        link_rows.append({'source_symbol':source,'adj_ratio':scale})
    result=[];counts={'raw_changed':0,'final_changed':0,'rows':0}
    for row in raw:
        source=row['symbol'];date=row['tradedate'];provider=aliases.get(source,source)
        if source not in scales or date<'2000-01-04':
            continue
        if row.get('close') is None or number(row['close'])<=0:
            continue
        factor=factors[provider].get(date)
        if factor is None or not math.isfinite(factor) or factor<=0:
            raise ValueError(f'Missing valid factor for {source} {date}')
        symbol=source.split('.')[1]+source.split('.')[0]
        canonical=final_by_key.get((symbol,date))
        if canonical is None:
            raise ValueError(f'Missing final price row for {source} {date}')
        raw_new=number(row['close'])*factor
        final_new=rounded(raw_new/scales[source])
        if not symbol.startswith('BJ') and date<'2019-01-01':
            final_new=number(canonical['adjclose'])
        counts['raw_changed']+=not math.isclose(raw_new,number(row['adjclose']),rel_tol=2e-7,abs_tol=1e-5)
        counts['final_changed']+=not math.isclose(final_new,number(canonical['adjclose']),rel_tol=2e-7,abs_tol=.0001)
        result.append({'source_symbol':source,'tradedate':date,'raw_adjclose':raw_new,
                       'final_symbol':symbol,'final_adjclose':final_new,
                       'old_raw_adjclose':row['adjclose'],'old_final_adjclose':canonical['adjclose']})
    counts['rows']=len(result)
    return result,link_rows,counts

def load_factors(inputs):
    factors={}
    for item in json.loads((inputs/'factor-summary.json').read_text()):
        rows=json.loads((inputs/item['file']).read_text())
        values={}
        for row in rows:
            date=row['trade_date'];date=date[:4]+'-'+date[4:6]+'-'+date[6:8]
            value=number(row['adj_factor'])
            if value<=0 or date in values:
                raise ValueError('Invalid or duplicate factor input')
            values[date]=value
        if not values:
            raise ValueError(f"Empty factor history: {item['provider']}")
        factors[item['provider']]=values
    return factors

def prepare(database,inputs,output,index_inputs=None):
    database.check_base();output.mkdir(parents=True,exist_ok=True)
    factors=load_factors(inputs)
    mapping=json.loads((inputs/'mapping.json').read_text())
    aliases={r['o_code']:r['n_code'] for r in mapping}
    providers=set(factors)
    sources=set(providers)|{old for old,new in aliases.items() if new in providers}
    quoted=','.join("'"+symbol+"'" for symbol in sorted(sources))
    links=database.query(f'SELECT * FROM ts_link_table WHERE link_symbol IN ({quoted}) ORDER BY link_symbol')
    actual={r['link_symbol'] for r in links}
    quoted=','.join("'"+symbol+"'" for symbol in sorted(actual))
    raw=database.query(f"SELECT symbol,tradedate,close,adjclose FROM ts_a_stock_eod_price WHERE symbol IN ({quoted}) AND tradedate>='2000-01-04' ORDER BY symbol,tradedate")
    final_symbols={r['w_symbol'] for r in links}
    final_quoted=','.join("'"+symbol+"'" for symbol in sorted(final_symbols))
    final=database.query(f"SELECT symbol,tradedate,close,adjclose FROM final_a_stock_eod_price WHERE symbol IN ({final_quoted}) AND tradedate>='2000-01-04' ORDER BY symbol,tradedate")
    wind=database.query(f"SELECT symbol,tradedate,adjclose FROM w_a_stock_eod_price WHERE symbol IN ({final_quoted}) AND tradedate>='2018-12-01' ORDER BY symbol,tradedate")
    rows,link_rows,counts=build_rows(raw,final,links,wind,factors,aliases)
    candidate_path=Path(__file__).with_name('source_repair_candidates.json')
    unresolved=[]
    patched={(r['final_symbol'],r['tradedate']):r for r in rows}
    for item in json.loads(candidate_path.read_text())['candidates']:
        before=patched.get((item['symbol'],item['previous_date']))
        after=patched.get((item['symbol'],item['break_date']))
        if not before or not after:
            raise ValueError(f"Missing candidate boundary: {item['symbol']}")
        change=after['final_adjclose']/before['final_adjclose']-1
        if abs(change)>.5:
            unresolved.append({'symbol':item['symbol'],'date':item['break_date'],'adjusted_return':change})
    write_csv(output/'adjustments.csv',list(rows[0]),rows)
    write_csv(output/'links.csv',['source_symbol','adj_ratio'],link_rows)
    factor_rows=[{'symbol':s,'tradedate':d,'adj_factor':v} for s,dates in sorted(factors.items()) for d,v in sorted(dates.items())]
    write_csv(output/'factors.csv',['symbol','tradedate','adj_factor'],factor_rows)
    write_csv(output/'mapping.csv',['o_code','n_code','name','list_date'],mapping)
    if index_inputs is not None:
        indices=json.loads((index_inputs/'index-repair-rows.json').read_text())
        for row in indices:
            date=row['trade_date'];row['trade_date']=date[:4]+'-'+date[4:6]+'-'+date[6:8]
        write_csv(output/'indices.csv',['index_code','stock_code','trade_date','weight'],indices)
        counts['index_rows']=len(indices)
    manifest={'base_commit':BASE,'source_run_id':42469384443,'index_run_id':42469829545,'code_revision':os.environ.get('GITHUB_SHA'),
              'counts':counts,'source_symbols':sorted(actual),
              'unresolved_boundaries':unresolved,'sha256':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in output.glob('*.csv')}}
    (output/'plan.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps(manifest),flush=True)

def apply(database,plan):
    database.check_base()
    manifest=json.loads((plan/'plan.json').read_text())
    if manifest['base_commit']!=BASE:
        raise ValueError('Invalid plan base')
    if manifest.get('unresolved_boundaries'):
        raise ValueError('Authoritative factors did not resolve every screened boundary')
    for name,digest in manifest['sha256'].items():
        if hashlib.sha256((plan/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Repair input checksum changed')
    database.run('checkout','-b','repair/source-data-2026-11-08')
    definitions={
        'repair_20261108_adjustments':('adjustments.csv','source_symbol VARCHAR(16),tradedate DATE,raw_adjclose DOUBLE,final_symbol VARCHAR(8),final_adjclose DOUBLE,old_raw_adjclose DOUBLE,old_final_adjclose DOUBLE,PRIMARY KEY(source_symbol,tradedate)'),
        'repair_20261108_links':('links.csv','source_symbol VARCHAR(16) PRIMARY KEY,adj_ratio DOUBLE'),
        'ts_adj_factor':('factors.csv','symbol VARCHAR(16),tradedate DATE,adj_factor DOUBLE,PRIMARY KEY(symbol,tradedate)'),
        'ts_bse_mapping':('mapping.csv','o_code VARCHAR(16) PRIMARY KEY,n_code VARCHAR(16),name VARCHAR(100),list_date VARCHAR(8)'),
    }
    for table,(name,definition) in definitions.items():
        database.run('sql','-q',f'CREATE TABLE {table} ({definition})')
        database.run('table','import','-u',table,str((plan/name).resolve()))
    database.run('sql','-q','CREATE TABLE repair_20261108_manifest (repair_id VARCHAR(64) PRIMARY KEY,base_commit VARCHAR(32),manifest_json TEXT)')
    serialized=json.dumps(manifest,sort_keys=True).replace("'","''")
    database.run('sql','-q',f"INSERT INTO repair_20261108_manifest VALUES ('source-data-2026-11-08','{BASE}','{serialized}')")
    if (plan/'indices.csv').exists():
        database.run('table','import','-u','ts_index_weight',str((plan/'indices.csv').resolve()))
    database.run('sql','-q',"UPDATE ts_a_stock_eod_price p JOIN repair_20261108_adjustments r ON p.symbol=r.source_symbol AND p.tradedate=r.tradedate SET p.adjclose=r.raw_adjclose")
    database.run('sql','-q',"UPDATE final_a_stock_eod_price p JOIN repair_20261108_adjustments r ON p.symbol=r.final_symbol AND p.tradedate=r.tradedate SET p.adjclose=r.final_adjclose")
    database.run('sql','-q',"UPDATE ts_link_table l JOIN repair_20261108_links r ON l.link_symbol=r.source_symbol SET l.adj_ratio=r.adj_ratio")
    checks={
        'raw_mismatch':"SELECT COUNT(*) n FROM ts_a_stock_eod_price p JOIN repair_20261108_adjustments r ON p.symbol=r.source_symbol AND p.tradedate=r.tradedate WHERE ABS(p.adjclose-r.raw_adjclose)>GREATEST(0.0001,ABS(r.raw_adjclose)*0.000001)",
        'final_mismatch':"SELECT COUNT(*) n FROM final_a_stock_eod_price p JOIN repair_20261108_adjustments r ON p.symbol=r.final_symbol AND p.tradedate=r.tradedate WHERE ABS(p.adjclose-r.final_adjclose)>GREATEST(0.0001,ABS(r.final_adjclose)*0.000001)",
    }
    for label,sql in checks.items():
        if database.query(sql)[0]['n']:
            raise ValueError(label)
    for table in ['ts_a_stock_eod_price','final_a_stock_eod_price']:
        same=' AND '.join(f'(from_{c} <=> to_{c})' for c in ['symbol','tradedate','open','high','low','close','volume','amount'])
        invalid=database.query(f"SELECT COUNT(*) n FROM dolt_diff('HEAD','WORKING','{table}') WHERE diff_type <> 'modified' OR NOT ({same})")[0]['n']
        if invalid:
            raise ValueError(f'Unexpected non-adjustment price changes in {table}')
    (plan/'dolt-diff-stat.txt').write_text(database.run('diff','--stat'))
    print(database.run('diff','--stat'),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('operation',choices=['plan','apply'])
    parser.add_argument('--database',required=True)
    parser.add_argument('--inputs')
    parser.add_argument('--index-inputs')
    parser.add_argument('--output',required=True)
    args=parser.parse_args();database=Database(args.database)
    if args.operation=='plan':prepare(database,Path(args.inputs),Path(args.output),Path(args.index_inputs) if args.index_inputs else None)
    else:apply(database,Path(args.output))
