from sqlalchemy import create_engine
import pymysql
import pandas as pd
import fire
import os

def dump_all_to_sqlib_source(skip_exists=False):
  sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
  dbConnection = sqlEngine.connect()

  index_map = {
    "csi300" : "399300.SZ",
    "csi500" : "000905.SH"
  }

  script_path = os.path.dirname(os.path.realpath(__file__))

  for index_name, index_code in index_map.items():
    filename = f'{script_path}/qlib_index/{index_name}.txt'
    print("Dumping to file: ", filename)
    if skip_exists and os.path.isfile(filename):
        continue
    sql = f"select concat(substr(stock_code, 8, 2), substr(stock_code, 1, 6)), min(trade_date), max(trade_date) FROM ts_index_weight WHERE index_code = '{index_code}' GROUP BY stock_code"
    stock_df = pd.read_sql(sql, dbConnection)
    stock_df.to_csv(filename, index=False, header=False, sep='\t')

if __name__ == "__main__":
  fire.Fire(dump_all_to_sqlib_source)