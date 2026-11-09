import importlib.util
from pathlib import Path
import unittest
from unittest import mock
import pandas as pd
import os
import tushare as ts

path=Path(__file__).resolve().parents[1]/'tushare/update_a_stock_eod_price_to_latest.py'
spec=importlib.util.spec_from_file_location('factor_anchor_test',path)
module=importlib.util.module_from_spec(spec)
with mock.patch.dict(os.environ,{'TUSHARE':'test'}),mock.patch.object(ts,'set_token'),mock.patch.object(ts,'pro_api'):
    spec.loader.exec_module(module)

class FactorAnchorTest(unittest.TestCase):
    def check(self,stored_factor,fresh_factor):
        stored=pd.DataFrame({'symbol':['920047.BJ'],'close':[13.21],'adjclose':[13.21*stored_factor]})
        fresh=pd.DataFrame({'ts_code':['920047.BJ'],'trade_date':['20261026'],'adj_factor':[fresh_factor]})
        with mock.patch.object(module.pandas,'read_sql',return_value=stored),mock.patch.object(module.pro,'adj_factor',return_value=fresh):
            module.verify_adjustment_anchor(mock.Mock(),'20261026')

    def test_restatement_is_rejected_before_another_day_can_be_appended(self):
        with self.assertRaisesRegex(RuntimeError,'history changed'):
            self.check(433.617,1.)

    def test_stable_factor_accepts_float_storage_rounding(self):
        self.check(1.14410005,1.1441)

    def test_issue163_one_day_restatement_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError,'history changed'):
            self.check(36.186273/21.51,1.5677)

    def test_nonpositive_or_missing_factor_is_rejected(self):
        for value in [0.,-1.,float('nan')]:
            with self.subTest(value=value),self.assertRaises(RuntimeError):
                self.check(1.,value)

if __name__=='__main__':unittest.main()
