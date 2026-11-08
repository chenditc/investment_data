import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import pandas as pd

path=Path(__file__).resolve().parents[1]/'qlib/dump_index_weight.py'
spec=importlib.util.spec_from_file_location('tested_index_transitions',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class IndexTransitionsTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('DOLT_BIN') or shutil.which('dolt'),'Dolt engine unavailable')
    def test_real_dolt_detects_late_constituent_change_and_return_to_prior_set(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);db=root/'db';db.mkdir();out=root/'index'
            binary=os.environ.get('DOLT_BIN') or shutil.which('dolt')
            def run(*args):return subprocess.check_output([binary,*args],cwd=db,text=True)
            run('init','--name','Test','--email','test@localhost')
            run('sql','-q','CREATE TABLE ts_index_weight (index_code VARCHAR(16),stock_code VARCHAR(16),trade_date DATE,PRIMARY KEY(index_code,stock_code,trade_date))')
            base=[f'{i:06d}.SZ' for i in range(1,151)]
            changed=base[:-1]+['000151.SZ']
            values=','.join(f"('399300.SZ','{symbol}','{date}')" for date,symbols in [('2026-01-30',base),('2026-02-27',changed),('2026-03-31',base)] for symbol in symbols)
            run('sql','-q','INSERT INTO ts_index_weight VALUES '+values)
            engine=mock.Mock();session=[]
            engine.raw_connection.return_value.cursor.return_value.execute.side_effect=lambda sql:session.append(sql)
            def read(sql,connection):
                result=json.loads(run('sql','-r','json','-q',';'.join(session+[sql])))
                return pd.DataFrame(result['rows'])
            with mock.patch.dict(module.INDEX_MAP,{'csi300':'399300.SZ'},clear=True),mock.patch.dict(os.environ,{'QLIB_INDEX_DIR':str(out)}),mock.patch.object(module,'create_engine',return_value=engine),mock.patch.object(module.pd,'read_sql_query',side_effect=read):
                module.dump_all_to_sqlib_source(target_trade_date='2026-03-31')
            text=(out/'csi300.txt').read_text()
            self.assertIn('SZ000150\t2026-01-30\t2026-02-26\n',text)
            self.assertIn('SZ000150\t2026-03-31\t2026-03-31\n',text)
            self.assertIn('SZ000151\t2026-02-27\t2026-03-30\n',text)
            self.assertEqual(len(text.splitlines()),450)

if __name__=='__main__':unittest.main()
