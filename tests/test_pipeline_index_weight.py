import datetime
import importlib.util
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

import pandas as pd
import tushare as ts


def load_module():
    path = Path(__file__).resolve().parents[1] / "tushare/dump_index_weight.py"
    spec = importlib.util.spec_from_file_location("tested_index_collector", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"TUSHARE": "test-token"}), \
            mock.patch.object(ts, "set_token"), mock.patch.object(ts, "pro_api"):
        spec.loader.exec_module(module)
    return module


MODULE = load_module()


class FixedDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 8, 17, 0, tzinfo=tz)


def snapshot(date="20261030", index="399300.SZ"):
    return pd.DataFrame({
        "index_code": [index], "con_code": ["600000.SH"],
        "trade_date": [date], "weight": [1.2],
    })


class IndexWeightCollectionTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pro = mock.Mock()
        self.pro.index_weight.side_effect = self.respond
        self.clock = types.SimpleNamespace(
            datetime=FixedDateTime, timedelta=datetime.timedelta, date=datetime.date,
        )
        for patch in [mock.patch.object(MODULE, "pro", self.pro),
                      mock.patch.object(MODULE, "file_path", str(self.root)),
                      mock.patch.object(MODULE, "index_list", ["399300.SZ"]),
                      mock.patch.object(MODULE, "datetime", self.clock),
                      mock.patch.object(MODULE.time, "sleep")]:
            patch.start()
            self.addCleanup(patch.stop)

    def respond(self, **parameters):
        if parameters["start_date"] <= "20261030" <= parameters["end_date"]:
            return snapshot()
        return snapshot().iloc[:0]

    def test_recent_partial_window_fetches_october_month_end(self):
        MODULE.dump_index_data(start_date=20260530)
        calls = [call.kwargs for call in self.pro.index_weight.call_args_list]
        self.assertEqual(calls[-1]["end_date"], "20261108")
        frame = pd.read_csv(self.root / "index_weight/399300.SZ.csv", dtype=str)
        self.assertEqual(frame["trade_date"].tolist(), ["20261030"])
        self.assertEqual(frame["stock_code"].tolist(), ["600000.SH"])

    def test_explicit_end_date_is_respected_and_windows_do_not_overlap(self):
        MODULE.dump_index_data(start_date="20261001", end_date="20261015")
        calls = [call.kwargs for call in self.pro.index_weight.call_args_list]
        self.assertEqual(calls[0]["start_date"], "20261001")
        self.assertEqual(calls[-1]["end_date"], "20261015")
        for left, right in zip(calls, calls[1:]):
            following = datetime.datetime.strptime(left["end_date"], "%Y%m%d") + datetime.timedelta(days=1)
            self.assertEqual(following.strftime("%Y%m%d"), right["start_date"])
        self.assertTrue(all(call["end_date"] <= "20261015" for call in calls))

    def test_empty_historical_windows_do_not_skip_later_available_snapshot(self):
        MODULE.dump_index_data(start_date="20250101")
        self.assertTrue((self.root / "index_weight/399300.SZ.csv").is_file())
        self.assertEqual(self.pro.index_weight.call_args_list[-1].kwargs["end_date"], "20261108")

    def test_empty_fetch_removes_a_previous_invocation_csv(self):
        directory = self.root / "index_weight"
        directory.mkdir()
        target = directory / "399300.SZ.csv"
        target.write_text("old data\n")
        self.pro.index_weight.side_effect = None
        self.pro.index_weight.return_value = snapshot().iloc[:0]
        MODULE.dump_index_data(start_date="20261101")
        self.assertFalse(target.exists())

    def test_unexpected_index_or_date_is_rejected(self):
        for frame in [snapshot(index="000905.SH"), snapshot(date="20260930")]:
            with self.subTest(frame=frame.to_dict()):
                self.pro.index_weight.side_effect = None
                self.pro.index_weight.return_value = frame
                with self.assertRaises(ValueError):
                    MODULE.dump_index_data(start_date="20261001", end_date="20261015")

    def test_import_csv_contains_only_database_columns(self):
        MODULE.dump_index_data(start_date="20261001", end_date="20261031")
        frame = pd.read_csv(self.root / "index_weight/399300.SZ.csv")
        self.assertEqual(frame.columns.tolist(), ["index_code", "stock_code", "trade_date", "weight"])

    def test_future_end_date_is_bounded_by_shanghai_today(self):
        MODULE.dump_index_data(start_date="20261101", end_date="20261231")
        self.assertTrue(all(call.kwargs["end_date"] <= "20261108" for call in self.pro.index_weight.call_args_list))


if __name__ == "__main__":
    unittest.main()
