from sqlalchemy import create_engine
import pymysql
import pandas as pd
import fire
import os
import datetime
from pathlib import Path

def dump_calendar_to_qlib_dir(qlib_dir, skip_exists=False):
  sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
  dbConnection = sqlEngine.connect()

  old_days_file =Path(qlib_dir) / "calendars/day.txt"
  old_calendar_df = pd.read_csv(old_days_file, header=None)
  min_date = pd.to_datetime(old_calendar_df.iloc[0][0])

  filename = Path(qlib_dir) / "calendars/day_future.txt"
  print("Dumping to file: ", filename)
  sql = "select date from ts_trade_day_calendar WHERE exchange = 'SSE' AND is_open = 1;"
  calendar_df = pd.read_sql(sql, dbConnection)
  calendar_df["date"] = pd.to_datetime(calendar_df["date"])
  calendar_df.drop(calendar_df[calendar_df["date"] < min_date].index, inplace=True)

  calendar_df.to_csv(filename, index=False, header=False, sep='\t')

if __name__ == "__main__":
  fire.Fire(dump_calendar_to_qlib_dir)