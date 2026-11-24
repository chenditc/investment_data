import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops import source_certificate as certificate
from ops import repair_provider_rows_20261124 as repair
from ops.install_provider_scale_20261123 import Database



class RepairPlanTest(unittest.TestCase):
    def test_committed_plan_is_scoped_to_unpublished_day(self):
        plan, rows = repair.load_plan()
        self.assertEqual(len(rows), 4723)
        self.assertEqual(plan['canonical_cells'], 2049)
        self.assertEqual(plan['raw_quote_fields_verified'], 33456)

    def test_changed_csv_fails_checksum(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'plan'; shutil.copytree(repair.PLAN, path)
            with (path / 'adjustments.csv').open('a') as stream: stream.write('bad\n')
            with self.assertRaisesRegex(ValueError, 'checksum'): repair.load_plan(path)

    def test_forged_rounding_or_another_date_fails_even_with_updated_digest(self):
        for field, value, message in [('final_adjclose', '999', 'link ratio'),
                                      ('tradedate', '2026-11-23', 'another date')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'plan'; shutil.copytree(repair.PLAN, path)
                with (path / 'adjustments.csv').open() as f: rows = list(csv.DictReader(f))
                rows[0][field] = value
                with (path / 'adjustments.csv').open('w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
                plan = json.loads((path / 'manifest.json').read_text())
                plan['adjustments_sha256'] = hashlib.sha256((path / 'adjustments.csv').read_bytes()).hexdigest()
                (path / 'manifest.json').write_text(json.dumps(plan))
                with self.assertRaisesRegex(ValueError, message): repair.load_plan(path)

    def test_manual_main_is_required(self):
        for ref, event in [('refs/heads/main', 'schedule'), ('refs/heads/other', 'workflow_dispatch')]:
            with mock.patch.dict(os.environ, {'GITHUB_REF':ref, 'GITHUB_EVENT_NAME':event}):
                with self.assertRaisesRegex(RuntimeError, 'manual main'): repair.main()


class EntryPointTest(unittest.TestCase):
    def test_daily_verification_imports_from_the_dolt_working_directory(self):
        line = next(line for line in (ROOT/'daily_update.sh').read_text().splitlines()
                    if 'ops.source_certificate attest' in line)
        command = (line.replace('/investment_data', shlex.quote(str(ROOT)))
                   .replace('python3', shlex.quote(sys.executable))
                   .replace(' attest', ' invalid-mode'))
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(['bash','-c',command],cwd=temporary,
                                    env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('usage: python3 -m ops.source_certificate',result.stderr)
        self.assertNotIn('ModuleNotFoundError',result.stderr)


@unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'), 'Dolt unavailable')
class RealDoltVerificationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name); path = self.root / 'db'; path.mkdir()
        self.db = Database(path)
        self.db.run('init', '--name', 'Test', '--email', 'test@localhost')
        self.db.run('config', '--local', '--add', 'user.name', 'Test')
        self.db.run('config', '--local', '--add', 'user.email', 'test@localhost')
        schema = ('symbol VARCHAR(16),tradedate DATE,open DOUBLE,high DOUBLE,low DOUBLE,'
                  'close DOUBLE,adjclose DOUBLE,volume DOUBLE,amount DOUBLE,PRIMARY KEY(symbol,tradedate)')
        for table in ('ts_a_stock_eod_price', 'final_a_stock_eod_price'):
            self.db.run('sql', '-q', f'CREATE TABLE {table} ({schema})')
        self.db.run('sql', '-q', 'CREATE TABLE ts_link_table (link_symbol VARCHAR(16) PRIMARY KEY,w_symbol VARCHAR(16),adj_ratio DOUBLE)')
        self.db.run('sql', '-q', 'CREATE TABLE ts_provider_factor_scale (symbol VARCHAR(16) PRIMARY KEY,scale DOUBLE)')
        for table in ('ts_index_weight', 'ts_provider_factor_scale_manifest'):
            self.db.run('sql', '-q', f'CREATE TABLE {table} (id INT PRIMARY KEY,v INT)')
        self.db.run('sql', '-q', "INSERT INTO ts_a_stock_eod_price VALUES ('000001.SZ','2026-11-23',9,11,8,10,20,100,1000)")
        self.db.run('sql', '-q', "INSERT INTO final_a_stock_eod_price VALUES ('SZ000001','2026-11-23',9,11,8,10,20,100,1000)")
        self.db.run('add','-A'); self.db.run('commit','-m','published fixture')
        self.good = self.db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']
        self.db.run('sql','-q',"INSERT INTO ts_a_stock_eod_price VALUES ('000001.SZ','2026-11-24',10,12,9,11,11,110,1100)")
        self.db.run('sql','-q',"INSERT INTO final_a_stock_eod_price VALUES ('SZ000001','2026-11-24',10,12,9,11,11,110,1100)")
        self.db.run('sql','-q',"INSERT INTO ts_provider_factor_scale VALUES ('000001.SZ',2)")
        self.db.run('sql','-q',"INSERT INTO ts_link_table VALUES ('000001.SZ','SZ000001',1)")
        self.db.run('add','-A'); self.db.run('commit','-m','unscaled external append')
        self.base = self.db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']

    def seed_complete_cohort(self):
        header=['symbol','tradedate','open','high','low','close','adjclose','volume','amount']
        for table, symbol in [('ts_a_stock_eod_price',lambda i:f'{i:06d}.SZ'),
                              ('final_a_stock_eod_price',lambda i:f'SZ{i:06d}')]:
            path=self.root/(table+'.csv')
            with path.open('w',newline='') as f:
                writer=csv.writer(f);writer.writerow(header)
                writer.writerows([[symbol(i),'2026-11-24',10,12,9,11,22,110,1100] for i in range(2,1003)])
            self.db.run('table','import','-u',table,str(path))
        path=self.root/'links.csv'
        with path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(['link_symbol','w_symbol','adj_ratio'])
            writer.writerows([[f'{i:06d}.SZ',f'SZ{i:06d}',1] for i in range(2,1003)])
        self.db.run('table','import','-u','ts_link_table',str(path))

    def test_missing_receipt_and_later_external_write_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'no successful collector'): certificate.verify(self.db)
        with mock.patch.object(certificate, 'check_provider_anchor'), mock.patch.object(certificate, 'latest_date', return_value='2026-11-24'):
            self.seed_complete_cohort()
            certificate.attest(self.db,'a'*40)
            self.assertEqual(certificate.verify(self.db)['result'],'verified')
            self.db.run('add','-A');self.db.run('commit','-m','verified fixture')
            certificate.attest(self.db,'b'*40)
            self.assertEqual(self.db.query('SELECT COUNT(*) n FROM dolt_status')[0]['n'],0)
            for table, sql in [
                ('price',"UPDATE final_a_stock_eod_price SET adjclose=99 WHERE symbol='SZ000001' AND tradedate='2026-11-24'"),
                ('raw',"UPDATE ts_a_stock_eod_price SET volume=99 WHERE symbol='000001.SZ' AND tradedate='2026-11-24'"),
                ('metadata',"UPDATE ts_provider_factor_scale SET scale=3 WHERE symbol='000001.SZ'")]:
                self.db.run('sql','-q',sql)
                with self.assertRaisesRegex(ValueError,'changed after collector'):certificate.verify(self.db)
                self.db.run('reset','--hard','HEAD')

    def test_incomplete_stock_cohort_does_not_create_receipt(self):
        with mock.patch.object(certificate, 'check_provider_anchor', side_effect=RuntimeError('factor mismatch')), \
                mock.patch.object(certificate, 'latest_date', return_value='2026-11-24'):
            # The cohort floor itself also rejects this small fixture.
            with self.assertRaisesRegex(ValueError, 'incomplete stock'):certificate.attest(self.db,'a'*40)
        tables={next(iter(r.values())) for r in self.db.query('SHOW TABLES')}
        self.assertNotIn(certificate.TABLE,tables)

    def test_failed_provider_anchor_does_not_certify_a_complete_cohort(self):
        self.seed_complete_cohort()
        with mock.patch.object(certificate,'check_provider_anchor',side_effect=RuntimeError('factor mismatch')):
            with self.assertRaisesRegex(RuntimeError,'factor mismatch'):certificate.attest(self.db,'a'*40)
        tables={next(iter(r.values())) for r in self.db.query('SHOW TABLES')}
        self.assertNotIn(certificate.TABLE,tables)

    def test_inconsistent_canonical_adjustment_prevents_certification(self):
        self.seed_complete_cohort()
        self.db.run('sql','-q',"UPDATE final_a_stock_eod_price SET adjclose=44 WHERE symbol='SZ000001' AND tradedate='2026-11-24'")
        with mock.patch.object(certificate,'check_provider_anchor'):
            with self.assertRaisesRegex(ValueError,'Canonical prices'):certificate.attest(self.db,'a'*40)

    def test_old_snapshot_keeps_legacy_path(self):
        self.db.run('reset','--hard',self.good)
        self.assertEqual(certificate.verify(self.db)['result'],'legacy_snapshot')

    def test_real_repair_preserves_history_is_idempotent_and_detects_tampering(self):
        planpath=self.root/'plan';planpath.mkdir()
        row=dict(symbol='000001.SZ',final_symbol='SZ000001',tradedate='2026-11-24',close=11,
                 provider_factor=1,scale=2,adj_ratio=1,old_raw_adjclose=11,raw_adjclose=22,
                 old_final_adjclose=11,final_adjclose=22)
        with (planpath/'adjustments.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(row));w.writeheader();w.writerow(row)
        plan=dict(base_commit=self.base,last_good_commit=self.good,date=repair.DATE,changes=1,
                  raw_cells=1,canonical_cells=1,total_rows=1,
                  adjustments_sha256=hashlib.sha256((planpath/'adjustments.csv').read_bytes()).hexdigest())
        (planpath/'manifest.json').write_text(json.dumps(plan))
        with mock.patch.object(repair,'BASE',self.base),mock.patch.object(repair,'GOOD',self.good):
            self.assertEqual(repair.apply(self.db,planpath),'updated')
            self.assertEqual(repair.apply(self.db,planpath),'unchanged')
            old=self.db.query("SELECT adjclose FROM final_a_stock_eod_price WHERE tradedate='2026-11-23'")
            self.assertEqual(old,[{'adjclose':20}])
            self.db.run('sql','-q',"UPDATE final_a_stock_eod_price SET adjclose=23 WHERE tradedate='2026-11-24'")
            self.db.run('add','-A');self.db.run('commit','-m','tamper')
            with self.assertRaisesRegex(ValueError,'installed value changed'):repair.apply(self.db,planpath)

    def test_changed_published_history_prevents_repair(self):
        self.db.run('sql','-q',"UPDATE final_a_stock_eod_price SET adjclose=21 WHERE tradedate='2026-11-23'")
        self.db.run('add','-A');self.db.run('commit','-m','unexpected historical edit')
        changedbase=self.db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']
        with mock.patch.object(repair,'BASE',changedbase),mock.patch.object(repair,'GOOD',self.good), \
                mock.patch.object(repair,'load_plan',return_value=({'changes':0},[])):
            with self.assertRaisesRegex(ValueError,'published price history'):repair.apply(self.db)


if __name__=='__main__':unittest.main()
