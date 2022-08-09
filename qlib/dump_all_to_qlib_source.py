from sqlalchemy import create_engine
import pymysql
import pandas as pd
import fire
import os

def dump_all_to_sqlib_source(skip_exists=True):
  sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
  dbConnection = sqlEngine.connect()
  universe = pd.read_sql("select symbol from final_a_stock_eod_price group by symbol", dbConnection)

  script_path = os.path.dirname(os.path.realpath(__file__))

  for symbol in universe["symbol"]:
    filename = f'{script_path}/qlib_source/{symbol}.csv'
    print("Dumping to file: ", filename)
    if skip_exists and os.path.isfile(filename):
        continue
    stock_df = pd.read_sql(f"select *, amount/volume*10 as vwap from final_a_stock_eod_price where symbol='{symbol}'", dbConnection)
    stock_df.to_csv(filename, index=False)

if __name__ == "__main__":
  fire.Fire(dump_all_to_sqlib_source)