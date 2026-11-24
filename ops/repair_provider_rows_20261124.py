"""Repair only the pinned, unpublished Nov24 adjustment cells from an older writer."""
import csv
import decimal
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from ops.install_provider_scale_20261123 import Database, equal

BASE = 'ce2nqentchb6rbqci7vgifcv419glbbn'
GOOD = 'ea2ldlk9beie1so13fqj5j4frt2epb8m'
DATE = '2026-11-24'
AUDIT = 'ts_provider_row_repairs'
PLAN = Path(__file__).resolve().parent / 'provider_rows_20261124'


def load_plan(directory=PLAN):
    plan = json.loads((directory / 'manifest.json').read_text())
    payload = (directory / 'adjustments.csv').read_bytes()
    if hashlib.sha256(payload).hexdigest() != plan['adjustments_sha256']:
        raise ValueError('Repair plan checksum mismatch')
    if (plan['base_commit'], plan['last_good_commit'], plan['date']) != (BASE, GOOD, DATE):
        raise ValueError('Unexpected repair identity')
    with (directory / 'adjustments.csv').open() as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != plan['changes'] or len({r['symbol'] for r in rows}) != len(rows):
        raise ValueError('Incomplete or duplicate repair cohort')
    for row in rows:
        if (not re.fullmatch(r'[0-9]{6}\.(SZ|SH|BJ)', row['symbol'])
                or row['final_symbol'] != row['symbol'][-2:] + row['symbol'][:6]
                or row['tradedate'] != DATE):
            raise ValueError('Repair attempts to change another date or security')
        numeric = ('close', 'provider_factor', 'scale', 'adj_ratio', 'old_raw_adjclose',
                   'raw_adjclose', 'old_final_adjclose', 'final_adjclose')
        if any(not math.isfinite(float(row[k])) or float(row[k]) <= 0 for k in numeric):
            raise ValueError('Invalid numeric repair value')
        if not equal(float(row['close']) * float(row['provider_factor']), row['old_raw_adjclose']):
            raise ValueError('Original adjustment is not on the captured provider basis')
        if not equal(float(row['old_raw_adjclose']) * float(row['scale']), row['raw_adjclose']):
            raise ValueError('Repair does not apply the registered conversion')
        for raw, canonical in (('old_raw_adjclose', 'old_final_adjclose'), ('raw_adjclose', 'final_adjclose')):
            value = decimal.Decimal(str(float(row[raw]) / float(row['adj_ratio'])))
            rounded = value.quantize(decimal.Decimal('.01'), rounding=decimal.ROUND_HALF_UP)
            if not equal(rounded, row[canonical]):
                raise ValueError('Canonical repair does not match its link ratio')
    return plan, rows


def verify_cells(db, rows, installed):
    raw = {r['symbol']: r for r in db.query(f"SELECT symbol,close,adjclose FROM ts_a_stock_eod_price WHERE tradedate='{DATE}'")}
    final = {r['symbol']: r for r in db.query(f"SELECT symbol,adjclose FROM final_a_stock_eod_price WHERE tradedate='{DATE}'")}
    scales = {r['symbol']: r for r in db.query('SELECT symbol,scale FROM ts_provider_factor_scale')}
    links = {r['link_symbol']: r for r in db.query('SELECT link_symbol,w_symbol,adj_ratio FROM ts_link_table')}
    for row in rows:
        symbol = row['symbol']
        prefix = '' if installed else 'old_'
        if (symbol not in raw or row['final_symbol'] not in final or symbol not in scales
                or not equal(raw[symbol]['close'], row['close'])
                or not equal(raw[symbol]['adjclose'], row[prefix + 'raw_adjclose'])
                or not equal(final[row['final_symbol']]['adjclose'], row[prefix + 'final_adjclose'])
                or not equal(scales[symbol]['scale'], row['scale'])
                or links[symbol]['w_symbol'] != row['final_symbol']
                or not equal(links[symbol]['adj_ratio'], row['adj_ratio'])):
            raise ValueError('Repair reference or installed value changed: ' + symbol)
    return len(raw), len(final)


def check_diff(db, table, rows, expected):
    canonical = table == 'final_a_stock_eod_price'
    by_symbol = {r['final_symbol' if canonical else 'symbol']: r for r in rows}
    diffs = db.query(f"SELECT * FROM dolt_diff('HEAD','WORKING','{table}')")
    if len(diffs) != expected:
        raise ValueError('Unexpected repair diff count: ' + table)
    for diff in diffs:
        symbol = diff['to_symbol']
        row = by_symbol.get(symbol)
        if (row is None or diff['diff_type'] != 'modified'
                or str(diff['to_tradedate'])[:10] != DATE
                or not equal(diff['to_adjclose'], row['final_adjclose' if canonical else 'raw_adjclose'])):
            raise ValueError('Unexpected adjustment repair diff')
        for field in ('symbol', 'tradedate', 'open', 'high', 'low', 'close', 'volume', 'amount'):
            if diff['from_' + field] != diff['to_' + field]:
                raise ValueError('Repair changed a raw quote or date: ' + field)


def apply(db, directory=PLAN):
    plan, rows = load_plan(directory)
    digest = hashlib.sha256((directory / 'manifest.json').read_bytes()).hexdigest()
    if db.query('SELECT COUNT(*) n FROM dolt_status')[0]['n']:
        raise ValueError('Repair requires a clean checkout')
    tables = {next(iter(r.values())) for r in db.query('SHOW TABLES')}
    if AUDIT in tables:
        receipts = db.query(f"SELECT plan_sha256 FROM {AUDIT} WHERE event_id='{DATE}'")
        if len(receipts) != 1 or receipts[0]['plan_sha256'] != digest:
            raise ValueError('Conflicting repair receipt')
        verify_cells(db, rows, True)
        return 'unchanged'
    if db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h'] != BASE:
        raise ValueError('Pinned repair source base changed')
    # All canonical changes since the last release must be newly added Nov24 rows.
    changed = db.query(f"SELECT COUNT(*) n FROM dolt_diff('{GOOD}','HEAD','final_a_stock_eod_price') "
                       f"WHERE diff_type <> 'added' OR to_tradedate <> '{DATE}'")[0]['n']
    if changed:
        raise ValueError('Previously published price history changed')
    if verify_cells(db, rows, False) != (plan['total_rows'], plan['total_rows']):
        raise ValueError('Repair day cohort changed')
    statements = ['START TRANSACTION;']
    for row in rows:
        for table, symbol, old_key, new_key in (
                ('ts_a_stock_eod_price', row['symbol'], 'old_raw_adjclose', 'raw_adjclose'),
                ('final_a_stock_eod_price', row['final_symbol'], 'old_final_adjclose', 'final_adjclose')):
            if not equal(row[old_key], row[new_key]):
                statements.append(f"UPDATE {table} SET adjclose={float(row[new_key])!r} "
                                  f"WHERE symbol='{symbol}' AND tradedate='{DATE}';")
    statements.append('COMMIT;')
    with tempfile.TemporaryDirectory() as temporary:
        sql = Path(temporary) / 'repair.sql'
        sql.write_text('\n'.join(statements))
        db.run('sql', '--file', str(sql))
    check_diff(db, 'ts_a_stock_eod_price', rows, plan['raw_cells'])
    check_diff(db, 'final_a_stock_eod_price', rows, plan['canonical_cells'])
    verify_cells(db, rows, True)
    db.run('sql', '-q', f'CREATE TABLE {AUDIT} (event_id VARCHAR(32) PRIMARY KEY,plan_sha256 VARCHAR(64),manifest_json LONGTEXT)')
    manifest = json.dumps(plan).replace("'", "''")
    db.run('sql', '-q', f"INSERT INTO {AUDIT} VALUES ('{DATE}','{digest}','{manifest}')")
    if {r['table_name'] for r in db.query('SELECT table_name FROM dolt_status')} != {
            AUDIT, 'ts_a_stock_eod_price', 'final_a_stock_eod_price'}:
        raise ValueError('Unexpected table changes during repair')
    db.run('add', '-A')
    db.run('commit', '-m', 'Align unpublished Nov24 adjustments from the legacy writer to the registered basis')
    return 'updated'


def main():
    if os.environ.get('GITHUB_REF') != 'refs/heads/main' or os.environ.get('GITHUB_EVENT_NAME') != 'workflow_dispatch':
        raise RuntimeError('Source repair requires manual main workflow')
    revision = Path('/opt/investment-data/REVISION').read_text().strip()
    if revision != os.environ.get('GITHUB_SHA') or subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip() != revision:
        raise RuntimeError('Source repair image revision mismatch')
    with Path('/dolt/.investment-data.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = Path('/dolt/investment_data')
        if not path.exists():
            subprocess.run(['dolt','clone','--depth','3','--branch','master','chenditc/investment_data',str(path)], check=True)
        db = Database(path)
        db.run('fetch','origin','master')
        db.run('reset','--hard','origin/master')
        result = apply(db)
        if result == 'updated':
            db.run('fetch','origin','master')
            if db.query("SELECT DOLT_HASHOF('origin/master') h")[0]['h'] != BASE:
                raise RuntimeError('Remote source changed before repair push')
            db.run('push','origin','master')
        Path(os.environ['DAILY_UPDATE_RESULT_FILE']).write_text(result+'\n')
        print(json.dumps({'result':result,'source_commit':db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']}))


if __name__ == '__main__':
    main()
