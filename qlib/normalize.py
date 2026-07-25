import fire
import pandas as pd
from sqlalchemy import create_engine
from concurrent.futures import ProcessPoolExecutor
import datetime
from typing import Optional

CALENDAR_START_DATE = pd.Timestamp("2000-01-04")
CALENDAR_EXCHANGE = "SSE"

try:
  from data_collector.base import Normalize
  from data_collector.yahoo import collector as yahoo_collector
except ImportError as e:
  print("============")
  print("ATTENTION: Need to put qlib/scripts directory into PYTHONPATH")
  print("============")
  raise e

class CrowdSourceNormalize(yahoo_collector.YahooNormalizeCN1d):
  # Add vwap so that vwap will be adjusted during normalization
  COLUMNS = ["open", "close", "high", "low", "vwap", "volume"]
  CALENDAR_LIST = None

  def _get_calendar_list(self):
    if self.CALENDAR_LIST is not None:
      return self.CALENDAR_LIST
    return super()._get_calendar_list()

  def _manual_adj_data(self, df: pd.DataFrame) -> pd.DataFrame:
    # amount should be kept as original value, so that adjusted volume * adjust vwap = amount
    result_df = super()._manual_adj_data(df)
    result_df["amount"] = df["amount"]
    return result_df


class _DateFieldAwareNormalize(Normalize):
  def format_data(self, df: pd.DataFrame) -> pd.DataFrame:
    if self.interval != "1d" or df.empty:
      return df

    value = df.iloc[-1][self._date_field_name]
    try:
      parsed = pd.to_datetime(value, format="%Y-%m-%d", errors="raise")
      if pd.isna(parsed):
        raise ValueError("invalid final date")
    except (TypeError, ValueError):
      return df.iloc[:-1]
    return df

  def _executor_with_source_cleanup(self, file_path):
    result = self._executor(file_path)
    if getattr(self, "_delete_source_after_success", False):
      file_path.unlink()
    return result

  def normalize(self):
    # Upstream uses filesystem enumeration order. Sorting keeps fixed-input
    # builds independent of directory iteration order.
    file_list = sorted(self._source_dir.glob("*.csv"), key=lambda path: path.as_posix())
    with ProcessPoolExecutor(max_workers=self._max_workers) as worker:
      for _ in worker.map(self._executor_with_source_cleanup, file_list):
        pass


def _canonical_date(value):
    if not isinstance(value, str):
        raise ValueError(f"Invalid target_trade_date: {value!r}")
    try:
        parsed = datetime.datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid target_trade_date: {value!r}") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise ValueError(f"Invalid target_trade_date: {value!r}")
    return value


def _load_trade_calendar_list(target_trade_date=None):
    if target_trade_date is not None:
        target_trade_date = _canonical_date(target_trade_date)

    sql_engine = create_engine("mysql+pymysql://root:@127.0.0.1/investment_data", pool_recycle=3600)
    db_connection = sql_engine.raw_connection()
    try:
        calendar_df = pd.read_sql(
            f"""
            SELECT date
            FROM ts_trade_day_calendar
            WHERE exchange = '{CALENDAR_EXCHANGE}'
              AND is_open = 1
              AND date >= '{CALENDAR_START_DATE.date()}'
              AND date <= {f"'{target_trade_date}'" if target_trade_date is not None else "CURRENT_DATE"}
            ORDER BY date
            """,
            db_connection,
        )
    finally:
        db_connection.close()
        sql_engine.dispose()

    calendar = pd.to_datetime(calendar_df["date"]).dt.normalize().tolist()
    if not calendar:
        raise ValueError(
            f"No open trade dates found for exchange={CALENDAR_EXCHANGE} on or after {CALENDAR_START_DATE.date()}"
        )
    return calendar


def normalize_crowd_source_data(
    source_dir=None,
    normalize_dir=None,
    max_workers=1,
    interval="1d",
    date_field_name="tradedate",
    symbol_field_name="symbol",
    target_trade_date: Optional[str] = None,
    delete_source_after_success=False,
):
    import multiprocessing as mp
    mp.set_start_method("spawn", force=True)
    CrowdSourceNormalize.CALENDAR_LIST = _load_trade_calendar_list(target_trade_date)
    yc = _DateFieldAwareNormalize(
        source_dir=source_dir,
        target_dir=normalize_dir,
        normalize_class=CrowdSourceNormalize,
        max_workers=max_workers,
        date_field_name=date_field_name,
        symbol_field_name=symbol_field_name,
        interval=interval,
    )
    yc._delete_source_after_success = delete_source_after_success
    yc.normalize()

if __name__ == "__main__":
    fire.Fire(normalize_crowd_source_data)
