from contextlib import contextmanager
import datetime
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd
import tushare as ts


def load_module():
    path = Path(__file__).resolve().parents[1] / "tushare/update_a_stock_eod_price_to_latest.py"
    spec = importlib.util.spec_from_file_location("tested_daily_update", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"TUSHARE": "test-token"}), \
            mock.patch.object(ts, "set_token"), mock.patch.object(ts, "pro_api"):
        spec.loader.exec_module(module)
    return module


MODULE = load_module()


def prices(trade_date="20261106"):
    return pd.DataFrame({
        "ts_code": ["600000.SH", "000001.SZ"], "trade_date": [trade_date] * 2,
        "open": [10.0, 20.0], "high": [11.0, 21.0], "low": [9.0, 19.0],
        "close": [10.5, 20.5], "vol": [100.0, 200.0], "amount": [1000.0, 2000.0],
    })


def factors(trade_date="20261106"):
    return pd.DataFrame({
        "ts_code": ["600000.SH", "000001.SZ"], "trade_date": [trade_date] * 2,
        "adj_factor": [2.0, 3.0],
    })


class DailyUpdateTest(unittest.TestCase):
    def setUp(self):
        self.pro = mock.Mock()
        self.pro.daily.return_value = prices()
        self.pro.adj_factor.return_value = factors()
        self.api_patch = mock.patch.object(MODULE, "pro", self.pro)
        self.sleep_patch = mock.patch.object(MODULE.time, "sleep")
        self.api_patch.start()
        self.sleep_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.addCleanup(self.sleep_patch.stop)

    def test_complete_prices_and_factors_preserve_every_symbol(self):
        result = MODULE.get_daily("20261106")
        self.assertEqual(result["ts_code"].tolist(), prices()["ts_code"].tolist())
        self.assertEqual(result["trade_date"].tolist(), ["20261106"] * 2)
        self.assertEqual(result["adj_close"].tolist(), [21.0, 61.5])

    def test_exhausted_requests_raise_instead_of_returning_none(self):
        failure = RuntimeError("upstream unavailable")
        self.pro.daily.side_effect = failure
        with self.assertRaisesRegex(RuntimeError, "20261106 after 3 attempts") as caught:
            MODULE.get_daily("20261106")
        self.assertEqual(self.pro.daily.call_count, 3)
        self.assertIs(caught.exception.__cause__, failure)

    def test_transient_request_failure_can_recover(self):
        self.pro.daily.side_effect = [RuntimeError("temporary outage"), prices()]
        self.assertEqual(len(MODULE.get_daily("20261106")), 2)
        self.assertEqual(self.pro.daily.call_count, 2)

    def test_missing_adjustment_factor_cannot_silently_drop_a_symbol(self):
        self.pro.adj_factor.return_value = factors().iloc[:1]
        with self.assertRaises(RuntimeError):
            MODULE.get_daily("20261106")
        self.assertEqual(self.pro.daily.call_count, 3)

    def test_wrong_factor_date_is_rejected(self):
        self.pro.adj_factor.return_value = factors("20261105")
        with self.assertRaises(RuntimeError):
            MODULE.get_daily("20261106")

    def test_invalid_prices_or_factors_are_rejected(self):
        null_price = prices()
        null_price.loc[0, "close"] = None
        zero_factor = factors()
        zero_factor.loc[0, "adj_factor"] = 0
        cases = [
            (null_price, factors()), (prices(), zero_factor),
            (prices("20261105"), factors()),
            (pd.concat([prices(), prices().iloc[:1]]), factors()),
        ]
        for price_frame, factor_frame in cases:
            with self.subTest(prices=price_frame, factors=factor_frame):
                self.pro.daily.return_value = price_frame
                self.pro.adj_factor.return_value = factor_frame
                with self.assertRaises(RuntimeError):
                    MODULE.get_daily("20261106")

    @contextmanager
    def database(self, latest="2026-11-04", dates=("20261104", "20261105", "20261106"), hour=18):
        clock = mock.Mock()
        clock.datetime.now.return_value = datetime.datetime(
            2026, 11, 6, hour, tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        engine = mock.Mock()
        latest_frame = pd.DataFrame({"tradedate": [pd.Timestamp(latest)]})
        with mock.patch.object(MODULE, "datetime", clock), \
                mock.patch.object(MODULE, "create_engine", return_value=engine), \
                mock.patch.object(MODULE.pandas, "read_sql", return_value=latest_frame), \
                mock.patch.object(MODULE, "get_trade_cal", return_value=pd.DataFrame({"cal_date": dates})), \
                mock.patch.object(pd.DataFrame, "to_sql", autospec=True, return_value=2) as write:
            yield write, clock
        engine.raw_connection.return_value.close.assert_called_once()

    def test_missing_historical_day_stops_before_fetching_or_writing_later_day(self):
        with self.database() as (write, _), \
                mock.patch.object(MODULE, "get_daily", return_value=pd.DataFrame()) as daily:
            with self.assertRaisesRegex(RuntimeError, "expected trading day 20261105"):
                MODULE.dump_astock_data()
            daily.assert_called_once_with("20261105")
            write.assert_not_called()

    def test_failed_historical_request_stops_before_later_day(self):
        with self.database() as (write, _):
            self.pro.daily.side_effect = RuntimeError("upstream unavailable")
            with self.assertRaises(RuntimeError):
                MODULE.dump_astock_data()
            self.assertEqual(self.pro.daily.call_args_list, [mock.call(trade_date="20261105")] * 3)
            write.assert_not_called()

    def test_intraday_run_defers_today_without_calling_daily_api(self):
        with self.database(latest="2026-11-05", dates=("20261105", "20261106"), hour=16) as (write, clock):
            MODULE.dump_astock_data()
            self.pro.daily.assert_not_called()
            write.assert_not_called()
            clock.datetime.now.assert_called_once_with(ZoneInfo("Asia/Shanghai"))

    def test_intraday_run_still_backfills_previous_day(self):
        self.pro.daily.return_value = prices("20261105")
        self.pro.adj_factor.return_value = factors("20261105")
        with self.database(hour=16) as (write, _):
            MODULE.dump_astock_data()
            self.pro.daily.assert_called_once_with(trade_date="20261105")
            write.assert_called_once()
            self.assertEqual(write.call_args.args[0]["tradedate"].tolist(), ["20261105"] * 2)

    def test_empty_today_after_publication_window_is_an_error(self):
        self.pro.daily.return_value = pd.DataFrame()
        with self.database(latest="2026-11-05", dates=("20261105", "20261106")) as (write, _):
            with self.assertRaisesRegex(RuntimeError, "expected trading day 20261106"):
                MODULE.dump_astock_data()
            write.assert_not_called()

    def test_successful_collection_writes_adjusted_prices_with_the_trade_date(self):
        with self.database(latest="2026-11-05", dates=("20261105", "20261106")) as (write, _):
            MODULE.dump_astock_data()
            write.assert_called_once()
            frame = write.call_args.args[0]
            self.assertEqual(frame["tradedate"].tolist(), ["20261106"] * 2)
            self.assertEqual(frame["adjclose"].tolist(), [21.0, 61.5])
            self.assertEqual(write.call_args.kwargs["if_exists"], "append")

    def test_empty_trading_calendar_cannot_report_success(self):
        with self.database(dates=()) as (write, _):
            with self.assertRaisesRegex(RuntimeError, "Trading calendar"):
                MODULE.dump_astock_data()
            write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
