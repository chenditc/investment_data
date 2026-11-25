"""Bind publication to the exact tables checked by the collector."""
import datetime
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sys

from ops.install_provider_scale_20261123 import Database

TABLE = 'ts_collection_verification'
TABLES = ('ts_a_stock_eod_price', 'final_a_stock_eod_price', 'ts_link_table',
          'ts_index_weight', 'ts_provider_factor_scale', 'ts_provider_factor_scale_manifest')
REQUIRED_FROM = '2026-11-24'
INDICES = {'399300.SZ','000300.SH','000905.SH','000906.SH','000852.SH','000985.SH'}


def latest_date(db):
    date = str(db.query('SELECT MAX(tradedate) d FROM final_a_stock_eod_price')[0]['d'])[:10]
    return datetime.date.fromisoformat(date).isoformat()


def table_hashes(db):
    result = {}
    tables = {next(iter(r.values())) for r in db.query('SHOW TABLES')}
    protected = TABLES + (('ts_provider_reconciliation',) if 'ts_provider_reconciliation' in tables else ())
    for table in protected:
        value = db.query("SELECT DOLT_HASHOF_TABLE('%s') h" % table)[0]['h']
        if not re.fullmatch('[0-9a-v]{32}', value):
            raise ValueError('Invalid source table hash: ' + table)
        result[table] = value
    return result


def verify(db):
    date = latest_date(db)
    tables = {next(iter(r.values())) for r in db.query('SHOW TABLES')}
    if TABLE not in tables:
        if date < REQUIRED_FROM:
            return {'result': 'legacy_snapshot', 'trade_date': date}
        raise ValueError('Source has no successful collector verification; run the collector before publication')
    rows = db.query(f'SELECT trade_date,table_hashes,git_revision FROM {TABLE} WHERE id=1')
    if len(rows) != 1 or str(rows[0]['trade_date'])[:10] != date:
        raise ValueError('Source date is not covered by collector verification')
    if not re.fullmatch('[0-9a-f]{40}', rows[0]['git_revision']):
        raise ValueError('Invalid collector verification revision')
    if json.loads(rows[0]['table_hashes']) != table_hashes(db):
        raise ValueError('Source tables changed after collector verification; run the collector before publication')
    return {'result': 'verified', 'trade_date': date}


def check_provider_anchor(db, date):
    # Import the collector only on the write side: the publisher has no API secret.
    import pymysql
    source = Path(__file__).resolve().parents[1] / 'tushare/update_a_stock_eod_price_to_latest.py'
    spec = importlib.util.spec_from_file_location('certificate_collector', source)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    connection = pymysql.connect(host='127.0.0.1', user='root', database='investment_data')
    try:
        scales = collector.load_factor_scales(connection)
        collector.verify_adjustment_anchor(connection, date.replace('-', ''), scales)
        fresh = collector.pro.daily(trade_date=date.replace('-', ''))
        if fresh.empty or not fresh['trade_date'].eq(date.replace('-', '')).all() or fresh['ts_code'].duplicated().any():
            raise ValueError('Incomplete provider quotes during source verification')
        expected = {row.ts_code:row for row in fresh.itertuples(index=False)}
        actual = {r['symbol']:r for r in db.query(
            f"SELECT symbol,open,high,low,close,volume,amount FROM ts_a_stock_eod_price WHERE tradedate='{date}'")
                  if r['symbol'] not in INDICES}
        if actual.keys() != expected.keys():
            raise ValueError('Source stock cohort differs from provider quotes')
        for symbol, row in actual.items():
            for stored, provider in [('open','open'),('high','high'),('low','low'),
                                     ('close','close'),('volume','vol'),('amount','amount')]:
                value = float(getattr(expected[symbol], provider))
                if not math.isfinite(value) or not math.isclose(float(row[stored]),value,rel_tol=1e-10,abs_tol=1e-6):
                    raise ValueError('Source quote differs from provider: '+symbol+' '+stored)
    finally:
        connection.close()


def check_canonical_rows(db, date):
    mismatch = [f'ABS(f.{field}-r.{field}) > GREATEST(ABS(r.{field})*1e-12,1e-8) '
                f'OR f.{field} IS NULL OR r.{field} IS NULL'
                for field in ('open','high','low','close','volume','amount')]
    mismatch += ['l.adj_ratio IS NULL', 'l.adj_ratio <= 0', 'f.adjclose IS NULL',
                 'ABS(f.adjclose-ROUND(r.adjclose/l.adj_ratio,2)) > 1e-8']
    query = ("SELECT COUNT(*) n FROM ts_a_stock_eod_price r "
             "LEFT JOIN ts_link_table l ON r.symbol=l.link_symbol "
             "LEFT JOIN final_a_stock_eod_price f ON f.symbol=l.w_symbol AND f.tradedate=r.tradedate "
             f"WHERE r.tradedate='{date}' AND ("+' OR '.join(mismatch)+')')
    if int(db.query(query)[0]['n']):
        raise ValueError('Canonical prices do not match raw quotes and registered link ratios')
    raw_n = int(db.query(f"SELECT COUNT(*) n FROM ts_a_stock_eod_price WHERE tradedate='{date}'")[0]['n'])
    final_n = int(db.query(f"SELECT COUNT(*) n FROM final_a_stock_eod_price WHERE tradedate='{date}'")[0]['n'])
    if raw_n != final_n:
        raise ValueError('Raw/canonical quote cohorts differ')


def attest(db, revision):
    if not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid collector image revision')
    date = latest_date(db)
    n = db.query("SELECT COUNT(*) n FROM final_a_stock_eod_price WHERE tradedate='%s'" % date)[0]['n']
    if int(n) <= 1000:
        raise ValueError('Cannot certify an incomplete stock snapshot')
    check_provider_anchor(db, date)
    check_canonical_rows(db, date)
    hashes = json.dumps(table_hashes(db), sort_keys=True, separators=(',', ':'))
    db.run('sql', '-q', f'''CREATE TABLE IF NOT EXISTS {TABLE} (
      id INT PRIMARY KEY,trade_date DATE NOT NULL,table_hashes LONGTEXT NOT NULL,
      git_revision VARCHAR(40) NOT NULL)''')
    # Avoid an unnecessary commit when an unchanged collector uses a newer image.
    previous = db.query(f'SELECT trade_date,table_hashes FROM {TABLE} WHERE id=1')
    if previous and str(previous[0]['trade_date'])[:10] == date and previous[0]['table_hashes'] == hashes:
        return verify(db)
    db.run('sql', '-q', f"REPLACE INTO {TABLE} VALUES (1,'{date}','{hashes}','{revision}')")
    return verify(db)


def main():
    if len(sys.argv) not in (2, 3) or sys.argv[1] not in ('attest', 'verify'):
        raise SystemExit('usage: python3 -m ops.source_certificate attest|verify [DOLT_CHECKOUT]')
    db = Database(Path(sys.argv[2]) if len(sys.argv) == 3 else '/dolt/investment_data')
    if sys.argv[1] == 'attest':
        result = attest(db, Path('/opt/investment-data/REVISION').read_text().strip())
    else:
        result = verify(db)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
