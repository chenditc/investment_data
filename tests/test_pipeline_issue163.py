import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('issue163', Path(__file__).resolve().parents[1]/'ops/repair_issue163.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    raw, final, factors, daily = [], [], {}, {}
    for date, price, adjusted in [('2025-10-30',21,32.9217), ('2025-10-31',21.51,36.186273), ('2025-11-03',21.21,33.250917)]:
        row = dict(symbol='920089.BJ',tradedate=date,open=price,high=price,low=price,close=price,volume=100,amount=1000,adjclose=adjusted)
        raw.append(row)
        final.append({**row,'symbol':'BJ920089','adjclose':round(adjusted,2)})
        factors['920089.BJ',date] = 1.5677
        daily['920089.BJ',date] = {**row,'vol':100}
    return raw, final, factors, daily


class Issue163Test(unittest.TestCase):
    def test_authoritative_factor_corrects_only_reported_day(self):
        changes = module.reconcile(*fixture())
        self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['tradedate'],'2025-10-31')
        self.assertAlmostEqual(changes[0]['raw_adjclose'],33.721227)
        self.assertEqual(changes[0]['final_adjclose'],33.72)

    def test_neighbor_restatement_and_changed_raw_prices_are_rejected(self):
        raw, final, factors, daily = fixture()
        factors['920089.BJ','2025-10-30'] = 1.6
        with self.assertRaisesRegex(ValueError,'neighboring anchor'):
            module.reconcile(raw,final,factors,daily)
        raw, final, factors, daily = fixture()
        daily['920089.BJ','2025-10-31']['close'] = 22
        with self.assertRaisesRegex(ValueError,'raw price mismatch'):
            module.reconcile(raw,final,factors,daily)

    @unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'),'Dolt engine unavailable')
    def test_real_dolt_repair_preserves_raw_fields_and_neighbor_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);dbdir=root/'db';dbdir.mkdir();out=root/'plan';out.mkdir()
            db=module.Database(str(dbdir))
            db.run('init','--name','Test','--email','test@localhost')
            raw,final,factors,daily=fixture()
            for table,rows in [('ts_a_stock_eod_price',raw),('final_a_stock_eod_price',final)]:
                db.run('sql','-q',f'CREATE TABLE {table} (symbol VARCHAR(16),tradedate DATE,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,amount DOUBLE,adjclose DOUBLE,PRIMARY KEY(symbol,tradedate))')
                columns=list(rows[0])
                path=root/(table+'.csv')
                module.write_csv(path,columns,rows)
                db.run('table','import','-u',table,str(path))
            db.run('add','-A');db.run('commit','-m','fixture')
            base=db.query("SELECT DOLT_HASHOF('HEAD') value")[0]['value']
            before={t:db.query(f'SELECT * FROM {t} ORDER BY tradedate') for t in ['ts_a_stock_eod_price','final_a_stock_eod_price']}
            changes=module.reconcile(raw,final,factors,daily)
            module.write_csv(out/'adjustments.csv',list(changes[0]),changes)
            module.write_csv(out/'factors.csv',['symbol','tradedate','adj_factor'],[{'symbol':s,'tradedate':d,'adj_factor':v} for (s,d),v in factors.items()])
            plan={'base_commit':base,'target_date':'2025-10-31','repair_rows':1,'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.csv')}}
            (out/'plan.json').write_text(json.dumps(plan))
            with mock.patch.object(module,'BASE',base):module.apply(db,out)
            for table in before:
                after=db.query(f'SELECT * FROM {table} ORDER BY tradedate')
                self.assertEqual(after[0],before[table][0]);self.assertEqual(after[2],before[table][2])
                self.assertEqual({k:v for k,v in after[1].items() if k!='adjclose'},{k:v for k,v in before[table][1].items() if k!='adjclose'})
            self.assertAlmostEqual(db.query("SELECT adjclose FROM ts_a_stock_eod_price WHERE tradedate='2025-10-31'")[0]['adjclose'],33.721227)
            self.assertEqual(db.query("SELECT adjclose FROM final_a_stock_eod_price WHERE tradedate='2025-10-31'")[0]['adjclose'],33.72)


if __name__ == '__main__':
    unittest.main()
