import copy
import datetime
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
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pandas as pd
from ops import reconcile_unpublished as module
from ops.install_provider_scale_20261123 import Database
from ops.source_certificate import table_hashes

NOW=datetime.datetime(2026,11,26,1,tzinfo=ZoneInfo('Asia/Shanghai'))


class PublishedManifestTest(unittest.TestCase):
    def test_daily_entrypoint_imports_from_an_unrelated_working_directory(self):
        line=next(x for x in (ROOT/'daily_update.sh').read_text().splitlines() if 'python3 -m ops.reconcile_unpublished' in x)
        command=line.replace('/investment_data',shlex.quote(str(ROOT))).replace('python3',shlex.quote(sys.executable)).replace('ops.reconcile_unpublished','ops.reconcile_unpublished invalid-mode')
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run(['bash','-c',command],cwd=tmp,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True)
        self.assertIn('usage: python3 -m ops.reconcile_unpublished',result.stderr)
        self.assertNotIn('ModuleNotFoundError',result.stderr)

    def fixture(self):
        value={'release_tag':'2026-11-24','target_trade_date':'2026-11-24',
               'dolt_commit':'a'*32,'investment_data_commit':'b'*40,'image_digest':'sha256:'+'c'*64,
               'archive_size_bytes':100,'archive_sha256':'sha256:'+'d'*64}
        payload=json.dumps(value).encode()
        release={'tag_name':'2026-11-24','draft':False,'prerelease':False,'assets':[
            {'id':1,'name':'qlib_bin.manifest.json','state':'uploaded','size':len(payload),'digest':'sha256:'+hashlib.sha256(payload).hexdigest()},
            {'id':2,'name':'qlib_bin.tar.gz','state':'uploaded','size':100,'digest':value['archive_sha256']}]}
        session=mock.Mock();session.get.side_effect=[mock.Mock(json=lambda:release),mock.Mock(content=payload)]
        return session,release,value

    def test_verified_publication_identity(self):
        session,_,_=self.fixture();result=module.latest_published(session)
        self.assertEqual(result['commit'],'a'*32)
        self.assertIn('/releases/assets/1',session.get.call_args.args[0])

    def test_modified_manifest_is_rejected(self):
        session,_,_=self.fixture()
        first,second=list(session.get.side_effect);second.content=b'{}';session.get.side_effect=[first,second]
        with self.assertRaisesRegex(ValueError,'checksum'):module.latest_published(session)

    def test_partial_or_draft_release_is_rejected(self):
        for partial in (True,False):
            session,release,_=self.fixture()
            if partial:release['assets'].pop()
            else:release['draft']=True
            with self.assertRaises(ValueError):module.latest_published(session)
            self.assertEqual(session.get.call_count,1)

    def test_manifest_must_agree_with_archive_metadata(self):
        session,release,_=self.fixture();release['assets'][1]['digest']='sha256:'+'e'*64
        with self.assertRaisesRegex(ValueError,'identity mismatch'):module.latest_published(session)

    def test_write_authority_rejects_other_branches_and_events(self):
        for ref,event in [('refs/heads/other','workflow_dispatch'),('refs/heads/main','push')]:
            with mock.patch.dict(os.environ,{'GITHUB_REF':ref,'GITHUB_EVENT_NAME':event}):
                with self.assertRaisesRegex(ValueError,'verified main'):module.check_authority()


@unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'),'Dolt unavailable')
class RealDoltReconciliationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'db';path.mkdir();self.db=Database(path)
        self.db.run('init','--name','Test','--email','test@localhost')
        self.db.run('config','--local','--add','user.name','Test');self.db.run('config','--local','--add','user.email','test@localhost')
        schema='symbol VARCHAR(16),tradedate DATE,open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,adjclose DOUBLE,volume DOUBLE,amount DOUBLE,PRIMARY KEY(symbol,tradedate)'
        for table in ('ts_a_stock_eod_price','final_a_stock_eod_price'):
            self.db.run('sql','-q',f'CREATE TABLE {table} ({schema})')
        self.db.run('sql','-q','CREATE TABLE ts_link_table (link_symbol VARCHAR(16) PRIMARY KEY,w_symbol VARCHAR(16),adj_ratio DOUBLE)')
        self.db.run('sql','-q','CREATE TABLE ts_provider_factor_scale (symbol VARCHAR(16) PRIMARY KEY,scale DOUBLE,effective_date DATE,reference_date DATE,reference_close DOUBLE,reference_adjclose DOUBLE)')
        for t in ('ts_provider_factor_scale_manifest','ts_index_weight'):
            self.db.run('sql','-q',f'CREATE TABLE {t} (id INT PRIMARY KEY,v INT)')
        self.db.run('sql','-q',"INSERT INTO ts_link_table VALUES ('000001.SZ','SZ000001',1),('000002.SZ','SZ000002',1)")
        self.db.run('sql','-q',"INSERT INTO ts_provider_factor_scale VALUES ('000001.SZ',2,'2026-11-23','2026-11-24',10,20),('000002.SZ',1,'2026-11-23','2026-11-24',20,20)")
        self.inputs={}
        self.calendar_dates=['20261124','20261125']
        for date,first,second in [('2026-11-24',10,20),('2026-11-25',11,21)]:
            prices=[]
            for i,close in [(1,first),(2,second)]:
                symbol=f'{i:06d}.SZ';volume=100+i;amount=volume*close/10
                adj=20 if date=='2026-11-24' else close
                for table,code in [('ts_a_stock_eod_price',symbol),('final_a_stock_eod_price',f'SZ{i:06d}')]:
                    self.db.run('sql','-q',f"INSERT INTO {table} VALUES ('{code}','{date}',{close},{close+1},{close-1},{close},{adj},{volume},{amount})")
                prices.append({'ts_code':symbol,'trade_date':date.replace('-',''),'open':close,'high':close+1,'low':close-1,'close':close,'vol':volume,'amount':amount})
            self.inputs['daily',date.replace('-','')]=prices
            self.inputs['adj_factor',date.replace('-','')]=[{'ts_code':x['ts_code'],'trade_date':date.replace('-',''),'adj_factor':1} for x in prices]
            self.commit('published' if date.endswith('24') else 'legacy append')
            if date.endswith('24'):
                self.published={'release_tag':date,'date':date,'commit':self.head(),'manifest_sha256':'sha256:'+'a'*64}
        self.minimum=mock.patch.object(module,'MIN_STOCKS',1);self.minimum.start();self.addCleanup(self.minimum.stop)
        self.authority=mock.Mock()

    def commit(self,message):self.db.run('add','-A');self.db.run('commit','-m',message)
    def head(self):return self.db.query("SELECT DOLT_HASHOF('HEAD') h")[0]['h']
    def provider(self):
        api=mock.Mock()
        for name in ('daily','adj_factor'):
            getattr(api,name).side_effect=lambda _name=name,**kw:pd.DataFrame(copy.deepcopy(self.inputs[_name,kw['trade_date']]))
        api.trade_cal.side_effect=lambda **kw:pd.DataFrame({'cal_date':list(self.calendar_dates)})
        return module.Provider(api)
    def reconcile(self):return module.reconcile(self.db,self.published,self.provider(),NOW,self.authority)
    def current(self):return self.db.query("SELECT adjclose FROM final_a_stock_eod_price WHERE symbol='SZ000001' AND tradedate='2026-11-25'")[0]['adjclose']

    def test_reconcile_preserves_published_history_and_is_idempotent(self):
        result=self.reconcile();self.assertEqual(result['result'],'reconciled');self.assertEqual(self.current(),22)
        self.assertEqual(result['days'][0]['raw_cells'],1);self.assertEqual(result['days'][0]['canonical_cells'],1)
        self.assertEqual(self.db.query("SELECT adjclose FROM final_a_stock_eod_price WHERE symbol='SZ000001' AND tradedate='2026-11-24'"),[{'adjclose':20}])
        self.assertIn(module.AUDIT,table_hashes(self.db))
        receipt=self.db.query(f'SELECT report_json,plan_sha256 FROM {module.AUDIT}')[0]
        self.assertEqual(module.digest(json.loads(receipt['report_json'])),receipt['plan_sha256'])
        self.commit('reconciled')
        self.assertEqual(self.reconcile()['result'],'unchanged');self.assertEqual(self.current(),22)
        self.assertEqual(self.db.query('SELECT COUNT(*) n FROM dolt_status')[0]['n'],0)

    def test_mixed_raw_and_canonical_bases_are_recognized_without_double_scaling(self):
        self.db.run('sql','-q',"UPDATE ts_a_stock_eod_price SET adjclose=22 WHERE symbol='000001.SZ' AND tradedate='2026-11-25'")
        self.commit('raw already aligned')
        result=self.reconcile();self.assertEqual(self.current(),22)
        self.assertEqual(result['days'][0]['raw_cells'],0);self.assertEqual(result['days'][0]['canonical_cells'],1)

    def test_published_price_edit_is_never_automatically_rewritten(self):
        self.db.run('sql','-q',"UPDATE final_a_stock_eod_price SET adjclose=19 WHERE tradedate='2026-11-24'")
        with self.assertRaisesRegex(ValueError,'Published canonical'):self.reconcile()
        self.assertEqual(self.current(),11);self.authority.assert_not_called()

    def test_published_raw_price_edit_is_rejected(self):
        self.db.run('sql','-q',"UPDATE ts_a_stock_eod_price SET adjclose=19 WHERE tradedate='2026-11-24'")
        with self.assertRaisesRegex(ValueError,'Published raw'):self.reconcile()

    def test_factor_metadata_changes_are_not_self_calibrated(self):
        self.db.run('sql','-q',"UPDATE ts_provider_factor_scale SET scale=3 WHERE symbol='000001.SZ'")
        with self.assertRaisesRegex(ValueError,'factor metadata'):self.reconcile()

    def test_existing_link_ratio_change_is_rejected(self):
        self.db.run('sql','-q',"UPDATE ts_link_table SET adj_ratio=2 WHERE link_symbol='000001.SZ'")
        with self.assertRaisesRegex(ValueError,'link ratios'):self.reconcile()

    def test_published_manifest_date_must_match_its_frozen_source(self):
        with self.assertRaisesRegex(ValueError,'Published source date'):
            module.make_plan(self.db,{**self.published,'date':'2026-11-23'},self.provider(),NOW)

    def test_financial_schema_change_is_rejected(self):
        self.db.run('sql','-q','ALTER TABLE final_a_stock_eod_price ADD COLUMN unknown DOUBLE')
        with self.assertRaisesRegex(ValueError,'schema changed'):self.reconcile()

    def test_unknown_factor_and_provider_restatement_are_rejected(self):
        self.inputs['adj_factor','20261124'][0]['adj_factor']=1.1
        with self.assertRaisesRegex(ValueError,'Trusted provider adjustment'):self.reconcile()
        self.assertEqual(self.current(),11)

    def test_unpublished_quote_mismatch_is_not_treated_as_factor_rebasing(self):
        self.inputs['daily','20261125'][0]['close']=12
        with self.assertRaisesRegex(ValueError,'differs from provider'):self.reconcile()
        self.assertEqual(self.current(),11)

    def test_unknown_current_adjustment_is_rejected(self):
        self.db.run('sql','-q',"UPDATE ts_a_stock_eod_price SET adjclose=13 WHERE symbol='000001.SZ' AND tradedate='2026-11-25'")
        with self.assertRaisesRegex(ValueError,'Trusted provider adjustment'):self.reconcile()

    def test_partial_stock_cohort_and_duplicate_provider_rows_are_rejected(self):
        self.inputs['daily','20261125'].pop()
        with self.assertRaisesRegex(ValueError,'stock cohort'):self.reconcile()
        self.inputs['daily','20261125'].append(copy.deepcopy(self.inputs['daily','20261125'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):self.reconcile()

    def test_not_due_source_day_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'not-due'):
            module.reconcile(self.db,self.published,self.provider(),NOW-datetime.timedelta(days=1),self.authority)

    def test_more_than_three_pending_days_are_rejected(self):
        for day in ('26','27','28'):
            self.db.run('sql','-q',f"INSERT INTO final_a_stock_eod_price VALUES ('SZ000001','2026-11-{day}',11,12,10,11,11,101,111.1)")
        with self.assertRaisesRegex(ValueError,'Too many unpublished'):self.reconcile()

    def test_missing_trading_day_cannot_be_skipped_by_an_external_append(self):
        for table in ('ts_a_stock_eod_price','final_a_stock_eod_price'):
            self.db.run('sql','-q',f"UPDATE {table} SET tradedate='2026-11-26' WHERE tradedate='2026-11-25'")
        self.calendar_dates.append('20261126')
        with self.assertRaisesRegex(ValueError,'Missing unpublished trading day'):
            module.reconcile(self.db,self.published,self.provider(),NOW+datetime.timedelta(days=1),self.authority)

    def test_new_unregistered_listing_has_no_historical_basis_to_convert(self):
        self.db.run('sql','-q',"INSERT INTO ts_link_table VALUES ('000003.SZ','SZ000003',1)")
        for table,symbol in [('ts_a_stock_eod_price','000003.SZ'),('final_a_stock_eod_price','SZ000003')]:
            self.db.run('sql','-q',f"INSERT INTO {table} VALUES ('{symbol}','2026-11-25',12,13,11,12,36,100,120)")
        self.commit('new listing')
        self.inputs['daily','20261125'].append({'ts_code':'000003.SZ','trade_date':'20261125','open':12,'high':13,'low':11,'close':12,'vol':100,'amount':120})
        self.inputs['adj_factor','20261125'].append({'ts_code':'000003.SZ','trade_date':'20261125','adj_factor':3})
        result=self.reconcile();self.assertEqual(result['days'][0]['stock_rows'],3)
        self.assertEqual(self.db.query("SELECT adjclose FROM final_a_stock_eod_price WHERE symbol='SZ000003'"),[{'adjclose':36}])

    def test_rounding_uses_the_same_double_types_as_price_columns(self):
        self.db.run('sql','-q','CREATE TABLE rounding_values (old_value DOUBLE,new_value DOUBLE,ratio DOUBLE)')
        self.db.run('sql','-q','INSERT INTO rounding_values VALUES (1.005,1.335,1)')
        expected=self.db.query('SELECT ROUND(old_value/ratio,2) old_value,ROUND(new_value/ratio,2) new_value FROM rounding_values')[0]
        actual=module.sql_round(self.db,[{'symbol':'000001.SZ','old_raw':1.005,'raw':1.335,'identity':1.005,'ratio':1}])['000001.SZ']
        self.assertEqual(actual[:2],(float(expected['old_value']),float(expected['new_value'])))

    def test_published_audit_history_is_append_only(self):
        self.reconcile();self.commit('published reconciled snapshot')
        published={**self.published,'date':'2026-11-25','release_tag':'2026-11-25','commit':self.head()}
        self.db.run('sql','-q',f"UPDATE {module.AUDIT} SET report_json='tampered'")
        with self.assertRaisesRegex(ValueError,'audit history changed'):
            module.make_plan(self.db,published,self.provider(),NOW)

    def test_unverified_write_authority_stops_before_financial_changes(self):
        with self.assertRaisesRegex(ValueError,'verified main'):
            module.reconcile(self.db,self.published,self.provider(),NOW,lambda:(_ for _ in ()).throw(ValueError('verified main required')))
        self.assertEqual(self.current(),11)

    def test_release_advancing_during_planning_prevents_writes(self):
        with mock.patch.object(module,'verify',side_effect=ValueError('unverified')), \
             mock.patch.object(module,'latest_published',side_effect=[self.published,{**self.published,'date':'2026-11-25'}]):
            with self.assertRaisesRegex(ValueError,'Published release changed'):
                module.reconcile(self.db,None,self.provider(),NOW,self.authority)
        self.assertEqual(self.current(),11)


if __name__=='__main__':unittest.main()
