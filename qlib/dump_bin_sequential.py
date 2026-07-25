#!/usr/bin/env python3
import argparse
import importlib.util
import os
from pathlib import Path

import pandas as pd


def _load_pinned_dump_module():
    path = Path(os.environ["QLIB_DUMP_BIN_PATH"]).resolve()
    if not path.is_file():
        raise ValueError(f"pinned qlib dump_bin.py is unavailable: {path}")
    spec = importlib.util.spec_from_file_location("pinned_qlib_dump_bin", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PINNED_DUMP = _load_pinned_dump_module()


class SequentialDumpDataAll(PINNED_DUMP.DumpDataAll):
    """Preserve qlib dump semantics without its eager process-pool deadlock."""

    def _get_all_date(self):
        all_datetime = set()
        date_range_list = []
        for file_path in self.df_files:
            (begin_time, end_time), calendars = self._get_date(
                file_path,
                as_set=True,
                is_begin_end=True,
            )
            all_datetime.update(calendars)
            if isinstance(begin_time, pd.Timestamp) and isinstance(
                end_time, pd.Timestamp
            ):
                symbol = self.get_symbol_from_file(file_path)
                date_range_list.append(
                    self.INSTRUMENTS_SEP.join(
                        (
                            symbol.upper(),
                            self._format_datetime(begin_time),
                            self._format_datetime(end_time),
                        )
                    )
                )
        self._kwargs["all_datetime_set"] = all_datetime
        self._kwargs["date_range_list"] = date_range_list

    def _dump_features(self):
        for file_path in self.df_files:
            self._dump_bin(file_path, self._calendars_list)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-path", required=True)
    parser.add_argument("--qlib-dir", required=True)
    parser.add_argument("--date-field-name", required=True)
    parser.add_argument("--exclude-fields", required=True)
    arguments = parser.parse_args()
    dumper = SequentialDumpDataAll(
        data_path=arguments.data_path,
        qlib_dir=arguments.qlib_dir,
        max_workers=1,
        date_field_name=arguments.date_field_name,
        exclude_fields=arguments.exclude_fields,
    )
    dumper.dump()


if __name__ == "__main__":
    main()
