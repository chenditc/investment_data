import fire
import pandas as pd

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

  def _manual_adj_data(self, df: pd.DataFrame) -> pd.DataFrame:
    # amount should be kept as original value, so that adjusted volume * adjust vwap = amount
    result_df = super()._manual_adj_data(df)
    result_df["amount"] = df["amount"]
    return result_df

def normalize_crowd_source_data(source_dir=None, normalize_dir=None, max_workers=1, interval="1d", date_field_name="tradedate", symbol_field_name="symbol"):
    yc = Normalize(
        source_dir=source_dir,
        target_dir=normalize_dir,
        normalize_class=CrowdSourceNormalize,
        max_workers=max_workers,
        date_field_name=date_field_name,
        symbol_field_name=symbol_field_name,
    )
    yc.normalize()

if __name__ == "__main__":
    fire.Fire(normalize_crowd_source_data)