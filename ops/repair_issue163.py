"""Reconcile BSE factors against pinned provider responses; change adjclose only."""
import argparse
import csv
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess

BASE = 'qlftrn2agatvmoo3ahr5gui8fds679jd'
DATES = ['2025-10-30', '2025-10-31', '2025-11-03']
TARGET = DATES[1]


def close(left, right):
    return math.isclose(float(left), float(right), rel_tol=2e-7, abs_tol=1e-6)


def reconcile(raw, final, factors, daily):
    final = {(r['symbol'][2:] + '.BJ', r['tradedate']): r for r in final}
    raw_keys = {(r['symbol'], r['tradedate']) for r in raw}
    if len(raw_keys) != len(raw) or raw_keys != set(final):
        raise ValueError('Missing or duplicate canonical rows')
    changes = []
    for row in raw:
        symbol, date = row['symbol'], row['tradedate']
        if not re.fullmatch(r'[0-9]{6}\.BJ', symbol) or date not in DATES:
            raise ValueError('Unexpected repair row')
        key = symbol, date
        factor = factors.get(key)
        provider = daily.get(key)
        if factor is None or not math.isfinite(factor) or factor <= 0 or provider is None:
            raise ValueError('Missing authoritative daily/factor pair')
        for field, source in [('open','open'), ('high','high'), ('low','low'),
                              ('close','close'), ('volume','vol'), ('amount','amount')]:
            if not close(row[field], provider[source]) or not close(final[key][field], row[field]):
                raise ValueError(f'Authoritative raw price mismatch: {symbol} {date} {field}')
        value = Decimal(str(row['close'])) * Decimal(str(factor))
        raw_new = float(value)
        final_new = float(value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP))
        raw_bad = not close(row['adjclose'], raw_new)
        final_bad = not close(final[key]['adjclose'], final_new)
        if not raw_bad and not final_bad:
            continue
        if date != TARGET:
            raise ValueError('Authoritative factors disagree with a neighboring anchor')
        changes.append({'source_symbol':symbol, 'tradedate':date,
                        'final_symbol':final[key]['symbol'], 'adj_factor':factor,
                        'old_raw_adjclose':float(row['adjclose']), 'raw_adjclose':raw_new,
                        'old_final_adjclose':float(final[key]['adjclose']), 'final_adjclose':final_new})
    return changes


class Database:
    def __init__(self, path):
        self.path = path
        self.binary = os.environ.get('DOLT_BIN', 'dolt')
    def run(self, *args):
        return subprocess.check_output([self.binary, *args], cwd=self.path, text=True)
    def query(self, sql):
        return json.loads(self.run('sql', '-r', 'json', '-q', sql))['rows']
    def check(self):
        if self.query("SELECT DOLT_HASHOF('HEAD') value")[0]['value'] != BASE:
            raise ValueError('Pinned source base changed')
        if 'nothing to commit, working tree clean' not in self.run('status'):
            raise ValueError('Dirty source checkout')


def write_csv(path, columns, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def prepare(db, inputs, output):
    db.check()
    output.mkdir(exist_ok=True, parents=True)
    manifest = json.loads((inputs / 'manifest.json').read_text())
    if len(manifest) != 6:
        raise ValueError('Incomplete provider inputs')
    factors, daily = {}, {}
    for item in manifest:
        path = inputs / item['file']
        if path.name != item['file'] or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Input checksum changed')
        destination = factors if item['api'] == 'adj_factor' else daily
        for row in json.loads(path.read_text()):
            date = row['trade_date']
            key = row['ts_code'], date[:4] + '-' + date[4:6] + '-' + date[6:]
            if key in destination:
                raise ValueError('Duplicate provider row')
            destination[key] = float(row['adj_factor']) if item['api'] == 'adj_factor' else row
    dates = ','.join("'" + d + "'" for d in DATES)
    fields = 'symbol,tradedate,open,high,low,close,adjclose,volume,amount'
    raw = db.query(f"SELECT {fields} FROM ts_a_stock_eod_price WHERE tradedate IN ({dates}) AND symbol LIKE '%.BJ' ORDER BY symbol,tradedate")
    final = db.query(f"SELECT {fields} FROM final_a_stock_eod_price WHERE tradedate IN ({dates}) AND symbol LIKE 'BJ%' ORDER BY symbol,tradedate")
    if [sum(r['tradedate'] == d for r in raw) for d in DATES] != [279,279,280]:
        raise ValueError('Unexpected source cohort size')
    changes = reconcile(raw, final, factors, daily)
    if not changes:
        raise ValueError('No repair rows')
    write_csv(output / 'adjustments.csv', list(changes[0]), changes)
    factor_rows = [{'symbol':s, 'tradedate':d, 'adj_factor':v} for (s,d),v in sorted(factors.items())]
    write_csv(output / 'factors.csv', ['symbol','tradedate','adj_factor'], factor_rows)
    result = {'base_commit':BASE, 'target_date':TARGET, 'source_run_id':42578998439,
              'provider_inputs':manifest, 'checked_rows':len(raw), 'repair_rows':len(changes),
              'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.csv')}}
    (output / 'plan.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


def apply(db, output):
    db.check()
    plan = json.loads((output / 'plan.json').read_text())
    if plan['base_commit'] != BASE or plan['target_date'] != TARGET:
        raise ValueError('Unexpected repair plan')
    for name, digest in plan['sha256'].items():
        if Path(name).name != name or hashlib.sha256((output/name).read_bytes()).hexdigest() != digest:
            raise ValueError('Repair plan checksum changed')
    db.run('checkout', '-b', 'repair/issue-163')
    db.run('sql', '-q', 'CREATE TABLE repair_issue163_adjustments (source_symbol VARCHAR(16),tradedate DATE,final_symbol VARCHAR(8),adj_factor DOUBLE,old_raw_adjclose DOUBLE,raw_adjclose DOUBLE,old_final_adjclose DOUBLE,final_adjclose DOUBLE,PRIMARY KEY(source_symbol,tradedate))')
    db.run('table','import','-u','repair_issue163_adjustments',str((output/'adjustments.csv').resolve()))
    for table, symbol, old in [('ts_a_stock_eod_price','source_symbol','old_raw_adjclose'), ('final_a_stock_eod_price','final_symbol','old_final_adjclose')]:
        rows = db.query(f'SELECT p.adjclose,r.{old} old_value,r.tradedate FROM {table} p JOIN repair_issue163_adjustments r ON p.symbol=r.{symbol} AND p.tradedate=r.tradedate')
        if len(rows) != plan['repair_rows'] or any(r['tradedate'] != TARGET or not close(r['adjclose'],r['old_value']) for r in rows):
            raise ValueError('Source values changed after planning')
    db.run('sql','-q','CREATE TABLE repair_issue163_factors (symbol VARCHAR(16),tradedate DATE,adj_factor DOUBLE,PRIMARY KEY(symbol,tradedate))')
    db.run('table','import','-u','repair_issue163_factors',str((output/'factors.csv').resolve()))
    db.run('sql','-q','CREATE TABLE repair_issue163_manifest (repair_id VARCHAR(32) PRIMARY KEY,base_commit VARCHAR(32),manifest_json TEXT)')
    value = json.dumps(plan, sort_keys=True).replace("'", "''")
    db.run('sql','-q',f"INSERT INTO repair_issue163_manifest VALUES ('issue-163','{BASE}','{value}')")
    for table, symbol, value in [('ts_a_stock_eod_price','source_symbol','raw_adjclose'), ('final_a_stock_eod_price','final_symbol','final_adjclose')]:
        db.run('sql','-q',f'UPDATE {table} p JOIN repair_issue163_adjustments r ON p.symbol=r.{symbol} AND p.tradedate=r.tradedate SET p.adjclose=r.{value}')
        same = ' AND '.join(f'(from_{c} <=> to_{c})' for c in ['symbol','tradedate','open','high','low','close','volume','amount'])
        invalid = db.query(f"SELECT COUNT(*) n FROM dolt_diff('HEAD','WORKING','{table}') WHERE diff_type <> 'modified' OR NOT ({same}) OR to_tradedate <> '{TARGET}'")[0]['n']
        count = db.query(f"SELECT COUNT(*) n FROM dolt_diff('HEAD','WORKING','{table}')")[0]['n']
        mismatch = db.query(f'SELECT p.adjclose,r.{value} expected FROM {table} p JOIN repair_issue163_adjustments r ON p.symbol=r.{symbol} AND p.tradedate=r.tradedate')
        if invalid or count != plan['repair_rows'] or any(not close(r['adjclose'],r['expected']) for r in mismatch):
            raise ValueError('Unexpected source mutation or incomplete adjustment repair')
    report = db.run('diff','--stat')
    (output/'dolt-diff-stat.txt').write_text(report)
    print(report, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['plan','apply'])
    parser.add_argument('--database', required=True)
    parser.add_argument('--inputs')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    db = Database(args.database)
    if args.operation == 'plan':
        prepare(db, Path(args.inputs), Path(args.output))
    else:
        apply(db, Path(args.output))
