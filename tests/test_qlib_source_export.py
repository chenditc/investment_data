import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest

import pandas as pd


def _load_module():
    try:
        import fire  # noqa: F401
    except ImportError:
        fire = types.ModuleType("fire")
        fire.Fire = lambda *_args, **_kwargs: None
        sys.modules["fire"] = fire
    path = Path(__file__).resolve().parents[1] / "qlib/dump_all_to_qlib_source.py"
    spec = importlib.util.spec_from_file_location("tested_qlib_source_export", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


class QlibSourceExportTest(unittest.TestCase):
    def test_split_symbols_are_appended_with_one_header(self):
        chunks = [
            pd.DataFrame(
                {
                    "symbol": ["SH600000", "SH600000", "SZ000001"],
                    "tradedate": ["2026-07-23", "2026-07-24", "2026-07-23"],
                    "close": [10.0, 10.1, 12.0],
                }
            ),
            pd.DataFrame(
                {
                    "symbol": ["SZ000001", "SZ000002"],
                    "tradedate": ["2026-07-24", "2026-07-24"],
                    "close": [12.1, 8.0],
                }
            ),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            row_count, symbol_count = MODULE._write_symbol_chunks(chunks, temporary)
            self.assertEqual(row_count, 5)
            self.assertEqual(symbol_count, 3)
            frame = pd.read_csv(Path(temporary) / "SZ000001.csv")
            self.assertEqual(frame["tradedate"].tolist(), ["2026-07-23", "2026-07-24"])
            self.assertEqual(
                (Path(temporary) / "SZ000001.csv")
                .read_text(encoding="utf-8")
                .count("symbol,tradedate,close"),
                1,
            )

    def test_empty_export_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "returned no rows"):
                MODULE._write_symbol_chunks([], temporary)

    def test_missing_symbol_column_fails_closed(self):
        chunks = [pd.DataFrame({"tradedate": ["2026-07-24"]})]
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "symbol column"):
                MODULE._write_symbol_chunks(chunks, temporary)

    def test_skip_exists_does_not_overwrite_preexisting_symbol(self):
        with tempfile.TemporaryDirectory() as temporary:
            existing = Path(temporary) / "SH600000.csv"
            existing.write_text("sentinel\n", encoding="utf-8")
            chunks = [
                pd.DataFrame(
                    {
                        "symbol": ["SH600000", "SZ000001"],
                        "tradedate": ["2026-07-24", "2026-07-24"],
                    }
                )
            ]
            row_count, symbol_count = MODULE._write_symbol_chunks(
                chunks,
                temporary,
                skip_exists=True,
            )
            self.assertEqual(row_count, 2)
            self.assertEqual(symbol_count, 1)
            self.assertEqual(existing.read_text(encoding="utf-8"), "sentinel\n")


if __name__ == "__main__":
    unittest.main()
