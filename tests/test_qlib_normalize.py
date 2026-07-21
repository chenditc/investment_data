import importlib.util
import inspect
from pathlib import Path
import sys
import types
from typing import Optional
import unittest
from unittest import mock

import pandas as pd


class _UpstreamNormalize:
    def __init__(
        self,
        source_dir=None,
        target_dir=None,
        normalize_class=None,
        max_workers=1,
        date_field_name="date",
        symbol_field_name="symbol",
        **kwargs,
    ):
        self._source_dir = Path(source_dir or ".")
        self._target_dir = Path(target_dir or ".")
        self._max_workers = max_workers
        self._date_field_name = date_field_name
        self._symbol_field_name = symbol_field_name
        self.interval = kwargs.get("interval", "1d")

    def normalize(self):
        return None


class _YahooNormalize:
    def _get_calendar_list(self):
        return []

    def _manual_adj_data(self, df):
        return df


def _load_module():
    try:
        import data_collector.base  # noqa: F401
        import data_collector.yahoo  # noqa: F401
    except ImportError:
        data_collector = types.ModuleType("data_collector")
        base = types.ModuleType("data_collector.base")
        base.Normalize = _UpstreamNormalize
        yahoo = types.ModuleType("data_collector.yahoo")
        yahoo.collector = types.SimpleNamespace(YahooNormalizeCN1d=_YahooNormalize)
        sys.modules.update(
            {
                "data_collector": data_collector,
                "data_collector.base": base,
                "data_collector.yahoo": yahoo,
            }
        )
    try:
        import fire  # noqa: F401
    except ImportError:
        fire = types.ModuleType("fire")
        fire.Fire = lambda *_args, **_kwargs: None
        sys.modules["fire"] = fire
    path = Path(__file__).resolve().parents[1] / "qlib/normalize.py"
    spec = importlib.util.spec_from_file_location("tested_qlib_normalize", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


class DateFieldAwareNormalizeTest(unittest.TestCase):
    def make_normalizer(self, interval="1d", date_field_name="tradedate"):
        normalizer = MODULE._DateFieldAwareNormalize.__new__(MODULE._DateFieldAwareNormalize)
        normalizer._date_field_name = date_field_name
        normalizer.interval = interval
        return normalizer

    def test_two_valid_tradedate_rows_are_retained(self):
        frame = pd.DataFrame(
            {"tradedate": ["2026-07-17", "2026-07-20"], "close": [1.0, 2.0]}
        )
        result = self.make_normalizer().format_data(frame)
        pd.testing.assert_frame_equal(result, frame)

    def test_invalid_final_value_removes_only_that_row(self):
        frame = pd.DataFrame(
            {"tradedate": ["2026-07-17", "not-a-date"], "close": [1.0, 2.0]}
        )
        result = self.make_normalizer().format_data(frame)
        pd.testing.assert_frame_equal(result, frame.iloc[:-1])

    def test_empty_dataframe_is_returned_unchanged(self):
        frame = pd.DataFrame(columns=["tradedate", "close"])
        self.assertIs(self.make_normalizer().format_data(frame), frame)

    def test_missing_configured_date_column_raises_key_error(self):
        frame = pd.DataFrame({"date": ["2026-07-20"]})
        with self.assertRaises(KeyError):
            self.make_normalizer().format_data(frame)

    def test_unexpected_parser_exception_propagates(self):
        frame = pd.DataFrame({"tradedate": ["2026-07-20"]})
        with mock.patch.object(MODULE.pd, "to_datetime", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                self.make_normalizer().format_data(frame)

    def test_non_daily_interval_is_returned_unchanged(self):
        frame = pd.DataFrame({"tradedate": ["not-a-date"]})
        self.assertIs(self.make_normalizer(interval="1m").format_data(frame), frame)

    def test_fire_signature_and_none_calendar_compatibility(self):
        parameter = inspect.signature(
            MODULE.normalize_crowd_source_data
        ).parameters["target_trade_date"]
        self.assertIsNone(parameter.default)
        self.assertEqual(parameter.annotation, Optional[str])

        engine = mock.Mock()
        connection = engine.raw_connection.return_value
        calendar = pd.DataFrame({"date": pd.to_datetime(["2026-07-17", "2026-07-20"])})
        with mock.patch.object(MODULE, "create_engine", return_value=engine), mock.patch.object(
            MODULE.pd, "read_sql", return_value=calendar
        ) as read_sql:
            self.assertEqual(
                MODULE._load_trade_calendar_list(None),
                list(calendar["date"]),
            )
        self.assertIn("date <= CURRENT_DATE", read_sql.call_args.args[0])
        connection.close.assert_called_once_with()
        engine.dispose.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
