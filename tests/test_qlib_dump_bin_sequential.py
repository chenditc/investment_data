import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd


def _load_module():
    temporary = tempfile.TemporaryDirectory()
    pinned = Path(temporary.name) / "dump_bin.py"
    pinned.write_text("class DumpDataAll:\n    pass\n", encoding="utf-8")
    path = Path(__file__).resolve().parents[1] / "qlib/dump_bin_sequential.py"
    spec = importlib.util.spec_from_file_location("tested_dump_bin_sequential", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, {"QLIB_DUMP_BIN_PATH": str(pinned)}):
        spec.loader.exec_module(module)
    return temporary, module


TEMPORARY, MODULE = _load_module()


class SequentialDumpDataAllTest(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        TEMPORARY.cleanup()

    def make_dumper(self):
        dumper = MODULE.SequentialDumpDataAll.__new__(MODULE.SequentialDumpDataAll)
        dumper.df_files = [Path("a.csv"), Path("b.csv")]
        dumper.INSTRUMENTS_SEP = "\t"
        dumper._kwargs = {}
        dumper._get_date = mock.Mock(
            side_effect=[
                (
                    (pd.Timestamp("2026-07-23"), pd.Timestamp("2026-07-24")),
                    {pd.Timestamp("2026-07-23"), pd.Timestamp("2026-07-24")},
                ),
                (
                    (pd.Timestamp("2026-07-24"), pd.Timestamp("2026-07-24")),
                    {pd.Timestamp("2026-07-24")},
                ),
            ]
        )
        dumper.get_symbol_from_file = mock.Mock(side_effect=["sh600000", "sz000001"])
        dumper._format_datetime = lambda value: value.strftime("%Y-%m-%d")
        return dumper

    def test_date_and_instrument_collection_matches_sorted_input_order(self):
        dumper = self.make_dumper()
        dumper._get_all_date()
        self.assertEqual(
            dumper._kwargs["all_datetime_set"],
            {pd.Timestamp("2026-07-23"), pd.Timestamp("2026-07-24")},
        )
        self.assertEqual(
            dumper._kwargs["date_range_list"],
            [
                "SH600000\t2026-07-23\t2026-07-24",
                "SZ000001\t2026-07-24\t2026-07-24",
            ],
        )

    def test_features_are_dumped_sequentially_in_file_order(self):
        dumper = self.make_dumper()
        dumper._calendars_list = [pd.Timestamp("2026-07-24")]
        dumper._dump_bin = mock.Mock()
        dumper._dump_features()
        self.assertEqual(
            dumper._dump_bin.call_args_list,
            [
                mock.call(Path("a.csv"), dumper._calendars_list),
                mock.call(Path("b.csv"), dumper._calendars_list),
            ],
        )


if __name__ == "__main__":
    unittest.main()
