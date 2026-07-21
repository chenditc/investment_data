import datetime
import os
import re
from pathlib import Path
from typing import Optional

import fire
import pandas as pd
from sqlalchemy import create_engine


INDEX_MAP = {
  "csi300": "399300.SZ",
  "csi500": "000905.SH",
  "csi800": "000906.SH",
  "csi1000": "000852.SH",
  "csiall": "000985.SH",
}
SYMBOL_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{6}\Z", re.ASCII)


def _validate_target_trade_date(target_trade_date: Optional[str]) -> Optional[str]:
  if target_trade_date is None:
    return None
  if not isinstance(target_trade_date, str):
    raise ValueError("target_trade_date must be YYYY-MM-DD")
  try:
    parsed = datetime.datetime.strptime(target_trade_date, "%Y-%m-%d")
  except ValueError as exc:
    raise ValueError("target_trade_date must be YYYY-MM-DD") from exc
  if parsed.strftime("%Y-%m-%d") != target_trade_date:
    raise ValueError("target_trade_date must be YYYY-MM-DD")
  return target_trade_date


def dump_all_to_sqlib_source(
    skip_exists=False,
    target_trade_date: Optional[str] = None,
):
  target_trade_date = _validate_target_trade_date(target_trade_date)
  final_end_date = target_trade_date or datetime.datetime.today().strftime("%Y-%m-%d")
  target_clause = ""
  if target_trade_date is not None:
    target_clause = f"AND trade_date <= '{target_trade_date}'"

  sql_engine = create_engine(
      "mysql+pymysql://root:@127.0.0.1/investment_data", pool_recycle=3600
  )
  db_connection = sql_engine.raw_connection()
  try:
    script_path = os.path.dirname(os.path.realpath(__file__))
    output_dir = Path(os.environ.get("QLIB_INDEX_DIR", f"{script_path}/qlib_index"))
    output_dir.mkdir(parents=True, exist_ok=True)

    for index_name, index_code in INDEX_MAP.items():
      filename = output_dir / f"{index_name}.txt"
      if skip_exists and filename.is_file():
        continue

      print("Dumping to file: ", filename)
      change_date_sql = f"""
        SELECT MIN(trade_date) AS change_date FROM
        (
          SELECT trade_date,
                 MD5(GROUP_CONCAT(stock_code ORDER BY stock_code SEPARATOR ',')) AS signature
          FROM ts_index_weight
          WHERE index_code = '{index_code}'
            {target_clause}
          GROUP BY trade_date
        ) date_sig_table
        GROUP BY signature
        ORDER BY change_date
      """
      change_dates = pd.read_sql_query(change_date_sql, db_connection)["change_date"]
      rows = []
      for index, change_date in enumerate(change_dates):
        start_date = pd.Timestamp(change_date).strftime("%Y-%m-%d")
        if index == len(change_dates) - 1:
          end_date = final_end_date
        else:
          end_date = (pd.Timestamp(change_dates.iloc[index + 1]) - datetime.timedelta(days=1)).strftime(
              "%Y-%m-%d"
          )
        if start_date > end_date:
          raise ValueError(f"Invalid index interval {index_name}: {start_date} > {end_date}")

        sql = f"""
          SELECT CONCAT(SUBSTR(stock_code, 8, 2), SUBSTR(stock_code, 1, 6)) AS symbol
          FROM ts_index_weight
          WHERE index_code = '{index_code}' AND trade_date = '{start_date}'
          ORDER BY stock_code
        """
        stock_df = pd.read_sql_query(sql, db_connection)
        if stock_df.empty:
          raise RuntimeError(f"No data for {index_code} at {start_date}")
        for symbol in stock_df["symbol"]:
          if not isinstance(symbol, str) or SYMBOL_RE.fullmatch(symbol) is None:
            raise ValueError(f"Malformed instrument symbol for {index_name}: {symbol!r}")
          rows.append((symbol, start_date, end_date))

      rows.sort(key=lambda row: (row[0], row[1], row[2]))
      if len(rows) != len(set(rows)):
        raise ValueError(f"Duplicate instrument rows for {index_name}")
      if not rows:
        raise ValueError(f"No index membership rows for {index_name}")
      with filename.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("".join("\t".join(row) + "\n" for row in rows))
  finally:
    db_connection.close()
    sql_engine.dispose()


if __name__ == "__main__":
  fire.Fire(dump_all_to_sqlib_source)
