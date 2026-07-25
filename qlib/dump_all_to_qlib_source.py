import fire
import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text


EXPORT_QUERY = """
select *, amount / volume * 10 as vwap
from final_a_stock_eod_price
order by symbol, tradedate
"""


def _write_symbol_chunks(chunks, output_dir, skip_exists=False):
  output_dir = Path(output_dir)
  output_dir.mkdir(parents=True, exist_ok=True)
  preexisting = {path.name for path in output_dir.glob("*.csv")} if skip_exists else set()
  emitted = set()
  row_count = 0

  for chunk in chunks:
    if chunk.empty:
      continue
    if "symbol" not in chunk.columns:
      raise ValueError("export query did not return a symbol column")
    row_count += len(chunk)
    for symbol, frame in chunk.groupby("symbol", sort=False):
      filename = f"{symbol}.csv"
      if filename in preexisting:
        continue
      path = output_dir / filename
      first_write = filename not in emitted
      frame.to_csv(
        path,
        index=False,
        mode="w" if first_write else "a",
        header=first_write,
      )
      emitted.add(filename)

  if row_count == 0:
    raise ValueError("final_a_stock_eod_price export returned no rows")
  if not emitted and not preexisting:
    raise ValueError("final_a_stock_eod_price export produced no symbol files")
  return row_count, len(emitted)


def dump_all_to_sqlib_source(skip_exists=False, chunk_rows=50000):
  if not isinstance(chunk_rows, int) or isinstance(chunk_rows, bool) or chunk_rows <= 0:
    raise ValueError("chunk_rows must be a positive integer")
  script_path = os.path.dirname(os.path.realpath(__file__))
  output_dir = os.environ.get("QLIB_SOURCE_DIR", f'{script_path}/qlib_source')

  sql_engine = create_engine(
    "mysql+pymysql://root:@127.0.0.1/investment_data",
    pool_recycle=3600,
  )
  try:
    with sql_engine.connect().execution_options(
      stream_results=True,
      max_row_buffer=chunk_rows,
    ) as connection:
      chunks = pd.read_sql_query(
        text(EXPORT_QUERY),
        connection,
        chunksize=chunk_rows,
      )
      row_count, symbol_count = _write_symbol_chunks(
        chunks,
        output_dir,
        skip_exists=skip_exists,
      )
  finally:
    sql_engine.dispose()

  print(
    f"Exported {row_count} rows into {symbol_count} symbol CSV files "
    f"with chunks of at most {chunk_rows} rows"
  )

if __name__ == "__main__":
  fire.Fire(dump_all_to_sqlib_source)
