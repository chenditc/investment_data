import importlib.util
from pathlib import Path
import unittest
import csv
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from unittest import mock

path=Path(__file__).resolve().parents[1]/'ops/repair_dolt_sources.py'
spec=importlib.util.spec_from_file_location('tested_source_repair',path)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SourceRepairTest(unittest.TestCase):
    def fixture(self):
        raw=[{'symbol':'430047.BJ','tradedate':'2023-10-13','close':10.,'adjclose':4300.},
             {'symbol':'920047.BJ','tradedate':'2026-10-26','close':13.21,'adjclose':5728.09},
             {'symbol':'920047.BJ','tradedate':'2026-10-27','close':12.62,'adjclose':12.62}]
        final=[dict(r,symbol=r['symbol'].split('.')[1]+r['symbol'].split('.')[0]) for r in raw]
        links=[{'link_symbol':'430047.BJ','w_symbol':'BJ430047','link_date':'2021-11-15','adj_ratio':1.},
               {'link_symbol':'920047.BJ','w_symbol':'BJ920047','link_date':'2025-10-09','adj_ratio':1.}]
        factors={'920047.BJ':{r['tradedate']:1. for r in raw}}
        return raw,final,links,[],factors,{'430047.BJ':'920047.BJ'}

    def test_current_factor_series_repairs_both_old_and_new_code(self):
        args=self.fixture()
        rows,links,counts=module.build_rows(*args)
        self.assertEqual([r['final_adjclose'] for r in rows],[10.,13.21,12.62])
        self.assertEqual([r['raw_adjclose'] for r in rows],[10.,13.21,12.62])
        self.assertEqual(counts['raw_changed'],2)
        self.assertEqual(counts['final_changed'],2)
        self.assertEqual(args[0][1]['adjclose'],5728.09)

    def test_missing_date_is_rejected(self):
        args=self.fixture();del args[4]['920047.BJ']['2026-10-26']
        with self.assertRaisesRegex(ValueError,'Missing valid factor'):
            module.build_rows(*args)

    def test_invalid_factor_is_rejected(self):
        for value in [0,-1,float('nan'),float('inf')]:
            args=self.fixture();args[4]['920047.BJ']['2026-10-26']=value
            with self.subTest(value=value),self.assertRaises(ValueError):
                module.build_rows(*args)

    def test_missing_final_row_is_rejected(self):
        args=self.fixture();args[1].pop()
        with self.assertRaisesRegex(ValueError,'Missing final'):
            module.build_rows(*args)

    def test_legacy_anchor_preserves_pre_2019_canonical_values(self):
        raw=[{'symbol':'600745.SH','tradedate':'2018-12-28','close':10.,'adjclose':500.},
             {'symbol':'600745.SH','tradedate':'2026-11-06','close':20.,'adjclose':2000.}]
        final=[{'symbol':'SH600745','tradedate':'2018-12-28','close':10.,'adjclose':100.},
               {'symbol':'SH600745','tradedate':'2026-11-06','close':20.,'adjclose':400.}]
        links=[{'link_symbol':'600745.SH','w_symbol':'SH600745','link_date':'2018-12-28','adj_ratio':5.}]
        factors={'600745.SH':{'2018-12-28':2.,'2026-11-06':2.}}
        wind=[{'symbol':'SH600745','tradedate':'2018-12-28','adjclose':100.}]
        rows,scales,counts=module.build_rows(raw,final,links,wind,factors,{})
        self.assertEqual(scales[0]['adj_ratio'],.2)
        self.assertEqual([r['final_adjclose'] for r in rows],[100.,200.])

    def test_inconsistent_reference_overlap_is_rejected(self):
        raw=[{'symbol':'600745.SH','tradedate':date,'close':10.,'adjclose':100.} for date in ['2018-12-27','2018-12-28']]
        final=[dict(r,symbol='SH600745') for r in raw]
        links=[{'link_symbol':'600745.SH','w_symbol':'SH600745','link_date':'2018-12-28','adj_ratio':1.}]
        factors={'600745.SH':{r['tradedate']:1. for r in raw}}
        wind=[{'symbol':'SH600745','tradedate':'2018-12-27','adjclose':200.},{'symbol':'SH600745','tradedate':'2018-12-28','adjclose':100.}]
        with self.assertRaisesRegex(ValueError,'Inconsistent canonical overlap'):
            module.build_rows(raw,final,links,wind,factors,{})

    def test_positive_price_rounding_matches_mysql_half_up(self):
        self.assertEqual(module.rounded(1.005),1.01)

    @unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'),'Dolt engine unavailable')
    def test_real_dolt_apply_preserves_ohlcv_amount_and_unaffected_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);db=root/'db';db.mkdir();plan=root/'plan';plan.mkdir()
            database=module.Database(db)
            database.run('init','--name','Test','--email','test@localhost')
            database.run('config','--local','--add','user.name','Test')
            database.run('config','--local','--add','user.email','test@localhost')
            schema='symbol VARCHAR(16),tradedate DATE,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,amount DOUBLE,adjclose DOUBLE,PRIMARY KEY(symbol,tradedate)'
            for table,symbol in [('ts_a_stock_eod_price','920047.BJ'),('final_a_stock_eod_price','BJ920047')]:
                database.run('sql','-q',f'CREATE TABLE {table} ({schema})')
                database.run('sql','-q',f"INSERT INTO {table} VALUES ('{symbol}','2026-10-26',13,14,12,13.21,100,1321,5728.09),('OTHER','2026-10-26',1,2,1,2,10,20,2)")
            database.run('sql','-q','CREATE TABLE ts_link_table (w_symbol VARCHAR(8),link_symbol VARCHAR(16) PRIMARY KEY,link_date DATE,adj_ratio DOUBLE)')
            database.run('sql','-q',"INSERT INTO ts_link_table VALUES ('BJ920047','920047.BJ','2025-10-09',1)")
            database.run('add','-A');database.run('commit','-m','fixture')
            base=database.query("SELECT DOLT_HASHOF('HEAD') AS value")[0]['value']
            rows=[{'source_symbol':'920047.BJ','tradedate':'2026-10-26','raw_adjclose':13.21,'final_symbol':'BJ920047','final_adjclose':13.21,'old_raw_adjclose':5728.09,'old_final_adjclose':5728.09}]
            module.write_csv(plan/'adjustments.csv',list(rows[0]),rows)
            module.write_csv(plan/'links.csv',['source_symbol','adj_ratio'],[{'source_symbol':'920047.BJ','adj_ratio':1}])
            module.write_csv(plan/'factors.csv',['symbol','tradedate','adj_factor'],[{'symbol':'920047.BJ','tradedate':'2026-10-26','adj_factor':1}])
            module.write_csv(plan/'mapping.csv',['o_code','n_code','name','list_date'],[{'o_code':'430047.BJ','n_code':'920047.BJ','name':'Test','list_date':'20201124'}])
            manifest={'base_commit':base,'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in plan.glob('*.csv')}}
            (plan/'plan.json').write_text(json.dumps(manifest))
            before=database.query('SELECT * FROM final_a_stock_eod_price ORDER BY symbol')
            with mock.patch.object(module,'BASE',base):module.apply(database,plan)
            after=database.query('SELECT * FROM final_a_stock_eod_price ORDER BY symbol')
            self.assertEqual(after[0]['adjclose'],13.21)
            before[0]['adjclose']=13.21
            self.assertEqual(before,after)

if __name__=='__main__':unittest.main()
