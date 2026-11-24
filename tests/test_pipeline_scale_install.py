"""Audit and real-Dolt regression checks for the fixed Nov23 metadata installation."""
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('scale_installer',ROOT/'ops/install_provider_scale_20261123.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class ScaleInstallTest(unittest.TestCase):
    def test_committed_plan_cohort_and_one_cell_scope(self):
        plan,rows,changes=module.load_plan()
        self.assertEqual(len(rows),5586)
        self.assertEqual(len(changes),1)
        self.assertEqual(changes[0]['symbol'],'002122.SZ')
        self.assertEqual(changes[0]['old_final_adjclose'],changes[0]['final_adjclose'])
        self.assertEqual(plan['current_stock_anchors_checked'],5570)

    def test_tampered_coefficients_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)
            shutil.copytree(module.PLAN,path,dirs_exist_ok=True)
            with (path/'scales.csv').open('a') as f:f.write('bad\n')
            with self.assertRaisesRegex(ValueError,'checksum'):
                module.load_plan(path)

    def test_wrong_scale_reference_is_rejected_even_with_recomputed_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);shutil.copytree(module.PLAN,path,dirs_exist_ok=True)
            with (path/'scales.csv').open() as f:rows=list(csv.DictReader(f))
            rows[0]['scale']='999'
            with (path/'scales.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=module.COLUMNS);w.writeheader();w.writerows(rows)
            plan=json.loads((path/'manifest.json').read_text());plan['scales_sha256']=hashlib.sha256((path/'scales.csv').read_bytes()).hexdigest();(path/'manifest.json').write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError,'reference'):
                module.load_plan(path)

    def test_installation_requires_manual_main_before_work(self):
        for ref,event in [('refs/heads/main','schedule'),('refs/heads/other','workflow_dispatch')]:
            with mock.patch.dict(os.environ,{'GITHUB_REF':ref,'GITHUB_EVENT_NAME':event}):
                with self.assertRaisesRegex(RuntimeError,'manual main'):
                    module.main()

    @unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'),'Dolt unavailable')
    def test_real_dolt_install_changes_one_raw_cell_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dbdir=root/'db';dbdir.mkdir();planpath=root/'plan';planpath.mkdir();db=module.Database(dbdir)
            db.run('init','--name','Test','--email','test@localhost');db.run('config','--local','--add','user.name','Test');db.run('config','--local','--add','user.email','test@localhost')
            schema='symbol VARCHAR(16),tradedate DATE,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,adjclose DOUBLE,volume DOUBLE,amount DOUBLE,PRIMARY KEY(symbol,tradedate)'
            db.run('sql','-q','CREATE TABLE ts_a_stock_eod_price ('+schema+')')
            db.run('sql','-q','CREATE TABLE final_a_stock_eod_price ('+schema+')')
            db.run('sql','-q','CREATE TABLE ts_link_table (symbol VARCHAR(16) PRIMARY KEY)')
            data=[];scales=[]
            for i in range(1,5587):
                symbol=f'{i:06d}.SZ'
                data.append([symbol,'2026-11-20',9,11,8,10,20,100,1000])
                scales.append(dict(zip(module.COLUMNS,[symbol,2,'2026-11-23','2026-11-20',10,20,1,1,'e5u75bq00p6u6t506du27p7k4ojueb2s'])))
                if i<=5570:data.append([symbol,'2026-11-23',9,11,8,10,20.001 if symbol=='002122.SZ' else 20,100,1000])
            rawpath=root/'raw.csv'
            with rawpath.open('w',newline='') as f:
                w=csv.writer(f);w.writerow(['symbol','tradedate','open','high','low','close','adjclose','volume','amount']);w.writerows(data)
            db.run('table','import','-u','ts_a_stock_eod_price',str(rawpath))
            db.run('sql','-q',"INSERT INTO final_a_stock_eod_price VALUES ('SZ002122','2026-11-23',9,11,8,10,20,100,1000)")
            db.run('add','-A');db.run('commit','-m','fixture');base=db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']
            with (planpath/'scales.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=module.COLUMNS);w.writeheader();w.writerows(scales)
            change=dict(symbol='002122.SZ',tradedate='2026-11-23',old_raw_adjclose=20.001,raw_adjclose=20,old_final_adjclose=20,final_adjclose=20)
            with (planpath/'adjustments.csv').open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(change));w.writeheader();w.writerow(change)
            plan=dict(base_commit=base,last_good_commit='e5u75bq00p6u6t506du27p7k4ojueb2s',effective_date='2026-11-23',rows=5586,
                      scales_sha256=hashlib.sha256((planpath/'scales.csv').read_bytes()).hexdigest(),adjustments_sha256=hashlib.sha256((planpath/'adjustments.csv').read_bytes()).hexdigest())
            (planpath/'manifest.json').write_text(json.dumps(plan))
            with mock.patch.object(module,'BASE',base):
                self.assertEqual(module.apply(db,planpath),'updated')
                self.assertEqual(module.apply(db,planpath),'unchanged')
                self.assertEqual(db.query("SELECT COUNT(*) n FROM dolt_diff('%s','HEAD','final_a_stock_eod_price')"%base)[0]['n'],0)
                self.assertEqual(db.query("SELECT COUNT(*) n FROM dolt_diff('%s','HEAD','ts_a_stock_eod_price')"%base)[0]['n'],1)
                db.run('sql','-q',"UPDATE ts_a_stock_eod_price SET adjclose=20.001 WHERE symbol='002122.SZ' AND tradedate='2026-11-23'")
                db.run('add','-A');db.run('commit','-m','revert correction fixture')
                with self.assertRaisesRegex(ValueError,'raw adjustment value changed'):
                    module.apply(db,planpath)
                db.run('sql','-q',"UPDATE ts_a_stock_eod_price SET adjclose=20 WHERE symbol='002122.SZ' AND tradedate='2026-11-23'")
                db.run('add','-A');db.run('commit','-m','restore correction fixture')
                db.run('sql','-q',"UPDATE ts_provider_factor_scale SET scale=3 WHERE symbol='000001.SZ'")
                db.run('add','-A');db.run('commit','-m','tamper fixture')
                with self.assertRaisesRegex(ValueError,'metadata changed'):
                    module.apply(db,planpath)

    def test_workflow_install_option_is_explicit_and_image_remains_pinned(self):
        import yaml
        text=(ROOT/'.github/workflows/data_update.yml').read_text();doc=yaml.load(text,Loader=yaml.BaseLoader)
        self.assertEqual(doc['on']['workflow_dispatch']['inputs']['operation']['options'],[
            'update','install-provider-scale-2026-11-23','repair-provider-rows-2026-11-24'])
        self.assertIn('"$IMAGE_REFERENCE"',text)
        self.assertIn('install-provider-scale-2026-11-23) python3 ops/install_provider_scale_20261123.py',text)
