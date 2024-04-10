from sqlalchemy import create_engine
import pymysql
import pandas as pd
import fire
import os
import datetime

def dump_all_to_sqlib_source(skip_exists=False):
  sqlEngine = create_engine('mysql+pymysql://root:@127.0.0.1/investment_data', pool_recycle=3600)
  dbConnection = sqlEngine.raw_connection()

  index_map = {
    "csi300" : "399300.SZ",
    "csi500" : "000905.SH",
    "csi800" : "000906.SH",
    "csi1000": "000852.SH",
    "csiall" : "000985.SH",
  }

  script_path = os.path.dirname(os.path.realpath(__file__))

  for index_name, index_code in index_map.items():
    filename = f'{script_path}/qlib_index/{index_name}.txt'
    if skip_exists and os.path.isfile(filename):
        continue

    print("Dumping to file: ", filename)
    change_date_sql = """
      select min(trade_date) as change_date from
      (
        select trade_date, MD5(GROUP_CONCAT(stock_code)) as signature
        from ts_index_weight 
        where index_code = '{}'
        group by trade_date
      ) date_sig_table
      group by signature
      order by change_date
    """.format(index_code)
    change_date_pd = pd.read_sql_query(change_date_sql, dbConnection)["change_date"]
    result_df_list = []
    for i in range(len(change_date_pd)):
      start_date = change_date_pd[i].strftime("%Y-%m-%d")
      if i == len(change_date_pd) - 1:
        end_date = datetime.datetime.today().strftime("%Y-%m-%d")
      else:
        end_date = (change_date_pd[i+1] - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

      sql = f"select concat(substr(stock_code, 8, 2), substr(stock_code, 1, 6)), '{start_date}' as start_date, '{end_date}' as end_date FROM ts_index_weight WHERE index_code = '{index_code}' AND trade_date = '{start_date}'"
      stock_df = pd.read_sql_query(sql, dbConnection)
      if stock_df.empty:
        raise Exception(f"No data for {sql}")
      result_df_list.append(stock_df)
    if len(result_df_list) > 0:
      pd.concat(result_df_list).to_csv(filename, index=False, header=False, sep='\t')

if __name__ == "__main__":
  fire.Fire(dump_all_to_sqlib_source)
