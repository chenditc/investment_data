from sqlalchemy import create_engine
import pymysql
import pandas as pd
import fire
import os

def dump_all_to_sqlib_source(skip_exists=True):
  sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
  dbConnection = sqlEngine.connect()
  stock_df = pd.read_sql("select *, amount/volume*10 as vwap from final_a_stock_eod_price", dbConnection)
  dbConnection.close()
  sqlEngine.dispose()

  script_path = os.path.dirname(os.path.realpath(__file__))

  for symbol, df in stock_df.groupby("symbol"):
    filename = f'{script_path}/qlib_source/{symbol}.csv'
    print("Dumping to file: ", filename)
    if skip_exists and os.path.isfile(filename):
        continue
    df.to_csv(filename, index=False)

if __name__ == "__main__":
  fire.Fire(dump_all_to_sqlib_source)
