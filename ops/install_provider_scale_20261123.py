"""Install a pinned, audited factor-basis conversion without rewriting price history."""
import csv
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

BASE = 'ibkrne7d73nal8pbg8ua3vu47nareksl'
TABLE = 'ts_provider_factor_scale'
AUDIT = 'ts_provider_factor_scale_manifest'
PLAN = Path(__file__).resolve().parent / 'provider_scale_20261123'
COLUMNS = ('symbol','scale','effective_date','reference_date','reference_close','reference_adjclose',
           'provider_reference_factor','provider_current_factor','reference_source_commit')


def load_plan(directory=PLAN):
    plan = json.loads((directory / 'manifest.json').read_text())
    if plan['base_commit'] != BASE or plan['effective_date'] != '2026-11-23' or plan['rows'] != 5586:
        raise ValueError('Unexpected provider scale plan')
    for name, key in [('scales.csv','scales_sha256'),('adjustments.csv','adjustments_sha256')]:
        if hashlib.sha256((directory/name).read_bytes()).hexdigest() != plan[key]:
            raise ValueError('Provider scale input checksum changed')
    with (directory/'scales.csv').open() as stream:
        reader=csv.DictReader(stream)
        if tuple(reader.fieldnames) != COLUMNS:
            raise ValueError('Provider scale columns changed')
        rows=list(reader)
    if len(rows) != plan['rows'] or len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError('Duplicate or missing provider scale rows')
    for row in rows:
        if re.fullmatch(r'[0-9]{6}\.(SZ|SH|BJ)',row['symbol']) is None or row['effective_date'] != plan['effective_date']:
            raise ValueError('Invalid provider scale identity')
        if datetime.date.fromisoformat(row['reference_date']).isoformat()!=row['reference_date'] or row['reference_date']>row['effective_date']:
            raise ValueError('Invalid provider reference date')
        if row['reference_source_commit'] not in (BASE,plan['last_good_commit']):
            raise ValueError('Unexpected provider reference source commit')
        values={k:float(row[k]) for k in ('scale','reference_close','reference_adjclose','provider_reference_factor','provider_current_factor')}
        if any(not math.isfinite(x) or x <= 0 for x in values.values()):
            raise ValueError('Invalid provider scale value')
        expected=values['reference_adjclose']/values['reference_close']/values['provider_reference_factor']
        if not math.isclose(values['scale'],expected,rel_tol=1e-12):
            raise ValueError('Provider scale does not match its reference')
    with (directory/'adjustments.csv').open() as stream:
        changes=list(csv.DictReader(stream))
    if len(changes)!=1 or changes[0]['symbol']!='002122.SZ' or changes[0]['tradedate']!='2026-11-23':
        raise ValueError('Unexpected adjustment correction scope')
    if float(changes[0]['old_final_adjclose']) != float(changes[0]['final_adjclose']):
        raise ValueError('Published canonical adjustment would change')
    return plan,rows,changes


class Database:
    def __init__(self,path):
        self.path=Path(path)
        self.binary=os.environ.get('DOLT_BIN','dolt')
    def run(self,*args):
        return subprocess.check_output([self.binary,*args],cwd=self.path,text=True)
    def query(self,sql):
        return json.loads(self.run('sql','-r','json','-q',sql))['rows']


def equal(a,b):
    return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-12)


def validate_references(db,rows,changes):
    by_date={}
    for row in rows:
        by_date.setdefault(row['reference_date'],{})[row['symbol']]=row
    for date,expected in sorted(by_date.items()):
        actual={r['symbol']:r for r in db.query("SELECT symbol,close,adjclose FROM ts_a_stock_eod_price WHERE tradedate='%s'"%date)}
        for symbol,ref in expected.items():
            if symbol not in actual or not equal(actual[symbol]['close'],ref['reference_close']) or not equal(actual[symbol]['adjclose'],ref['reference_adjclose']):
                raise ValueError('Provider reference changed: '+symbol+' '+date)
    scales={r['symbol']:r for r in rows}
    corrections={r['symbol']:r for r in changes}
    current=db.query("SELECT symbol,close,adjclose FROM ts_a_stock_eod_price WHERE tradedate='2026-11-23'")
    checked=0
    for bar in current:
        if bar['symbol'] not in scales:
            if bar['symbol'] not in ('399300.SZ','000300.SH','000905.SH','000906.SH','000852.SH','000985.SH'):
                raise ValueError('Unmapped current source bar')
            continue
        ref=scales[bar['symbol']]
        change=corrections.get(bar['symbol'])
        if change and not equal(bar['adjclose'],change['old_raw_adjclose']):
            raise ValueError('Adjustment correction base changed')
        value=float(change['raw_adjclose'] if change else bar['adjclose'])/float(bar['close'])
        aligned=float(ref['provider_current_factor'])*float(ref['scale'])
        if abs(value-aligned)>abs(aligned)*.00001+.000001:
            raise ValueError('Current source anchor does not match registered scale')
        checked+=1
    if checked!=5570:
        raise ValueError('Incomplete current source cohort')


def apply(db,directory=PLAN):
    plan,rows,changes=load_plan(directory)
    digest=hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest()
    if db.query('SELECT COUNT(*) n FROM dolt_status')[0]['n'] != 0:
        raise ValueError('Source working tree is dirty')
    tables={next(iter(r.values())) for r in db.query('SHOW TABLES')}
    if TABLE in tables or AUDIT in tables:
        if TABLE not in tables or AUDIT not in tables:
            raise ValueError('Partial provider scale installation')
        installed=db.query(f"SELECT plan_sha256 FROM {AUDIT} WHERE event_id='2026-11-23'")
        if len(installed)!=1 or installed[0]['plan_sha256']!=digest or db.query(f'SELECT COUNT(*) n FROM {TABLE}')[0]['n']!=plan['rows']:
            raise ValueError('Different provider scale installation already exists')
        actual={r['symbol']:r for r in db.query(f'SELECT symbol,scale,effective_date FROM {TABLE}')}
        if any(r['symbol'] not in actual or not equal(r['scale'],actual[r['symbol']]['scale'])
               or str(actual[r['symbol']]['effective_date'])[:10]!=r['effective_date'] for r in rows):
            raise ValueError('Installed provider scale metadata changed')
        return 'unchanged'
    if db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']!=BASE:
        raise ValueError('Pinned source base changed')
    validate_references(db,rows,changes)
    db.run('sql','-q',f'''CREATE TABLE {TABLE} (
      symbol VARCHAR(16) PRIMARY KEY,scale DOUBLE NOT NULL,effective_date DATE NOT NULL,
      reference_date DATE NOT NULL,reference_close DOUBLE NOT NULL,reference_adjclose DOUBLE NOT NULL,
      provider_reference_factor DOUBLE NOT NULL,provider_current_factor DOUBLE NOT NULL,
      reference_source_commit VARCHAR(32) NOT NULL)''')
    db.run('table','import','-u',TABLE,str((directory/'scales.csv').resolve()))
    db.run('sql','-q',f'CREATE TABLE {AUDIT} (event_id VARCHAR(32) PRIMARY KEY,source_commit VARCHAR(32),plan_sha256 VARCHAR(64),manifest_json LONGTEXT)')
    manifest=json.dumps(plan,ensure_ascii=False).replace("'","''")
    db.run('sql','-q',f"INSERT INTO {AUDIT} VALUES ('2026-11-23','{BASE}','{digest}','{manifest}')")
    change=changes[0]
    db.run('sql','-q',"UPDATE ts_a_stock_eod_price SET adjclose=%s WHERE symbol='002122.SZ' AND tradedate='2026-11-23' AND adjclose=%s"%(change['raw_adjclose'],change['old_raw_adjclose']))
    # The only financial write is the reviewed unpublished-day raw adjustment cell.
    diffs=db.query("SELECT * FROM dolt_diff('HEAD','WORKING','ts_a_stock_eod_price')")
    if len(diffs)!=1 or diffs[0]['diff_type']!='modified':
        raise ValueError('Unexpected raw price diff count')
    diff=diffs[0]
    for field in ('symbol','tradedate','open','high','low','close','volume','amount'):
        if diff['from_'+field]!=diff['to_'+field]:
            raise ValueError('Unexpected financial field change: '+field)
    if diff['to_symbol']!='002122.SZ' or str(diff['to_tradedate'])[:10]!='2026-11-23' or not equal(diff['to_adjclose'],change['raw_adjclose']):
        raise ValueError('Unexpected raw adjustment correction')
    for table in ('final_a_stock_eod_price','ts_link_table'):
        if db.query(f"SELECT COUNT(*) n FROM dolt_diff('HEAD','WORKING','{table}')")[0]['n']!=0:
            raise ValueError('Canonical prices or links unexpectedly changed')
    # Catch any unexpected existing table mutation before making the commit.
    status=db.query('SELECT table_name FROM dolt_status')
    if {x['table_name'] for x in status}!={TABLE,AUDIT,'ts_a_stock_eod_price'}:
        raise ValueError('Unexpected changed tables')
    db.run('add',TABLE,AUDIT,'ts_a_stock_eod_price')
    db.run('commit','-m','Register verified provider factor basis; preserve canonical price history')
    return 'updated'


def main():
    if os.environ.get('GITHUB_REF')!='refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch':
        raise RuntimeError('Provider scale installation requires manual main workflow')
    revision=Path('/opt/investment-data/REVISION').read_text().strip()
    if revision!=os.environ.get('GITHUB_SHA') or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()!=revision:
        raise RuntimeError('Provider scale installation image revision mismatch')
    with Path('/dolt/.investment-data.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        path=Path('/dolt/investment_data')
        if not path.exists():
            subprocess.run(['dolt','clone','--depth','1','--branch','master','chenditc/investment_data',str(path)],check=True)
        db=Database(path)
        db.run('fetch','origin','master')
        db.run('reset','--hard','origin/master')
        result=apply(db)
        if result=='updated':
            db.run('fetch','origin','master')
            if db.query("SELECT DOLT_HASHOF('origin/master') h")[0]['h']!=BASE:
                raise RuntimeError('Remote source changed before push')
            db.run('push','origin','master')
        Path(os.environ['DAILY_UPDATE_RESULT_FILE']).write_text(result+'\n')
        print(json.dumps({'result':result,'source_commit':db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']}))


if __name__=='__main__':
    main()
