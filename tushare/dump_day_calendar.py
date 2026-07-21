import datetime
from pathlib import Path
from typing import Optional

import fire
import pandas as pd
from sqlalchemy import create_engine


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


def dump_calendar_to_qlib_dir(
    qlib_dir,
    skip_exists=False,
    target_trade_date: Optional[str] = None,
):
  del skip_exists  # Retained for Fire compatibility.
  target_trade_date = _validate_target_trade_date(target_trade_date)

  qlib_path = Path(qlib_dir)
  day_path = qlib_path / "calendars/day.txt"
  old_calendar = [line for line in day_path.read_text(encoding="utf-8").splitlines() if line]
  if not old_calendar:
    raise ValueError("Current qlib calendar is empty")
  min_date = pd.Timestamp(old_calendar[0]).strftime("%Y-%m-%d")
  current_end = pd.Timestamp(old_calendar[-1]).strftime("%Y-%m-%d")
  if target_trade_date is not None and current_end != target_trade_date:
    raise ValueError(
        f"Current qlib calendar ends at {current_end}, expected {target_trade_date}"
    )

  sql_engine = create_engine(
      "mysql+pymysql://root:@127.0.0.1/investment_data", pool_recycle=3600
  )
  db_connection = sql_engine.raw_connection()
  try:
    sql = f"""
      SELECT date
      FROM ts_trade_day_calendar
      WHERE exchange = 'SSE' AND is_open = 1 AND date >= '{min_date}'
      ORDER BY date
    """
    calendar_df = pd.read_sql(sql, db_connection)
  finally:
    db_connection.close()
    sql_engine.dispose()

  dates = [pd.Timestamp(value).strftime("%Y-%m-%d") for value in calendar_df["date"]]
  if not dates or dates != sorted(set(dates)):
    raise ValueError("Future calendar dates must be nonempty, ordered, and unique")
  if target_trade_date is not None and target_trade_date not in dates:
    raise ValueError(f"Target trade date {target_trade_date} is absent from the calendar")

  filename = qlib_path / "calendars/day_future.txt"
  print("Dumping to file: ", filename)
  with filename.open("w", encoding="utf-8", newline="\n") as stream:
    stream.write("".join(f"{value}\n" for value in dates))


if __name__ == "__main__":
  fire.Fire(dump_calendar_to_qlib_dir)
