"""Preserve canonical returns across an explicitly registered provider basis change."""
import datetime
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock
import pandas as pd
import tushare as ts

path = Path(__file__).resolve().parents[1] / 'tushare/update_a_stock_eod_price_to_latest.py'
spec = importlib.util.spec_from_file_location('factor_scale_test', path)
module = importlib.util.module_from_spec(spec)
with mock.patch.dict(os.environ, {'TUSHARE': 'test'}), mock.patch.object(ts, 'set_token'), mock.patch.object(ts, 'pro_api'):
    spec.loader.exec_module(module)


class FactorScaleTest(unittest.TestCase):
    def check_anchor(self, stored_factor, provider_factor, scales):
        stored = pd.DataFrame({'symbol': ['920010.BJ'], 'close': [7.53], 'adjclose': [7.53 * stored_factor]})
        fresh = pd.DataFrame({'ts_code': ['920010.BJ'], 'trade_date': ['20261123'], 'adj_factor': [provider_factor]})
        with mock.patch.object(module.pandas, 'read_sql', return_value=stored), mock.patch.object(module.pro, 'adj_factor', return_value=fresh):
            module.verify_adjustment_anchor(mock.Mock(), '20261123', scales)

    def test_registered_rebase_preserves_the_existing_factor(self):
        self.check_anchor(3.8839, 1.1431, {'920010.BJ': (3.8839 / 1.1431, '20261123')})

    def test_unregistered_rebase_still_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'history changed'):
            self.check_anchor(3.8839, 1.1431, {})

    def test_registered_scale_does_not_hide_a_new_restatement(self):
        with self.assertRaisesRegex(RuntimeError, 'stored_factor.*aligned_provider_factor'):
            self.check_anchor(3.8839, 1.0, {'920010.BJ': (3.8839 / 1.1431, '20261123')})

    def test_conversion_applies_only_on_or_after_effective_date(self):
        frame = pd.DataFrame({'ts_code': ['A', 'B'], 'adj_factor': [2., 3.]})
        scales = {'A': (4., '20261123')}
        self.assertEqual(module.align_factor_basis(frame, '20261120', scales)['adj_factor'].tolist(), [2., 3.])
        self.assertEqual(module.align_factor_basis(frame, '20261123', scales)['adj_factor'].tolist(), [8., 3.])
        self.assertEqual(frame['adj_factor'].tolist(), [2., 3.])

    def test_scale_table_absent_keeps_legacy_strict_behavior(self):
        with mock.patch.object(module.pandas, 'read_sql', return_value=pd.DataFrame({'n': [0]})):
            self.assertEqual(module.load_factor_scales(mock.Mock()), {})

    def test_scale_records_must_be_unique_positive_finite_and_nonempty(self):
        for values in ([0.], [-1.], [float('nan')], [float('inf')], [1., 2.], []):
            with self.subTest(values=values):
                rows = pd.DataFrame({'symbol': ['A'] * len(values), 'scale': values, 'effective_date': ['2026-11-23'] * len(values)})
                with mock.patch.object(module.pandas, 'read_sql', side_effect=[pd.DataFrame({'n': [1]}), rows]):
                    with self.assertRaises(RuntimeError):
                        module.load_factor_scales(mock.Mock())

    def test_new_daily_adjusted_close_uses_registered_scale_without_changing_quotes(self):
        from test_pipeline_daily_update import prices, factors
        with mock.patch.object(module.pro, 'daily', return_value=prices()), mock.patch.object(module.pro, 'adj_factor', return_value=factors()):
            frame = module.get_daily('20261106', {'600000.SH': (3., '20261101')})
        self.assertEqual(frame['adj_close'].tolist(), [63., 61.5])
        self.assertEqual(frame['close'].tolist(), prices()['close'].tolist())
        self.assertEqual(frame['vol'].tolist(), prices()['vol'].tolist())
        self.assertEqual(frame['amount'].tolist(), prices()['amount'].tolist())

    def test_unregistered_new_listing_needs_no_old_factor(self):
        with mock.patch.object(module.pandas, 'read_sql', return_value=pd.DataFrame()), mock.patch.object(module.pro, 'adj_factor') as api:
            module.verify_unmapped_anchors(mock.Mock(), pd.DataFrame({'symbol': ['NEW']}), {'OLD': (2., '20261123')})
        api.assert_not_called()

    def test_unregistered_resuming_history_must_match_provider(self):
        stored = pd.DataFrame({'symbol': ['A'], 'tradedate': [datetime.date(2026, 11, 20)], 'close': [10.], 'adjclose': [4000.]})
        fresh = pd.DataFrame({'ts_code': ['A'], 'trade_date': ['20261120'], 'adj_factor': [1.]})
        with mock.patch.object(module.pandas, 'read_sql', return_value=stored), mock.patch.object(module.pro, 'adj_factor', return_value=fresh):
            with self.assertRaisesRegex(RuntimeError, 'Unregistered provider factor basis'):
                module.verify_unmapped_anchors(mock.Mock(), pd.DataFrame({'symbol': ['A']}), {'OLD': (2., '20261123')})

    def test_unregistered_identity_basis_is_accepted(self):
        stored = pd.DataFrame({'symbol': ['A'], 'tradedate': [datetime.date(2026, 11, 20)], 'close': [10.], 'adjclose': [20.]})
        fresh = pd.DataFrame({'ts_code': ['A'], 'trade_date': ['20261120'], 'adj_factor': [2.]})
        with mock.patch.object(module.pandas, 'read_sql', return_value=stored), mock.patch.object(module.pro, 'adj_factor', return_value=fresh):
            module.verify_unmapped_anchors(mock.Mock(), pd.DataFrame({'symbol': ['A']}), {'OLD': (2., '20261123')})
