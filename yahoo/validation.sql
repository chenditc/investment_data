CREATE TABLE `yahoo_link_table` (
  `symbol` varchar(100) NOT NULL,
  `link_date` date NOT NULL,
  `adj_ratio` double,
  PRIMARY KEY (`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin



/* Populate link_table */
INSERT INTO yahoo_link_table (symbol, link_date)
select symbol, max(tradedate) as link_date 
from yahoo_a_stock_eod_price
WHERE VOLUME > 0
group by symbol;

/* Change symbol to upper case */
UPDATE yahoo_link_table SET symbol = UPPER(symbol)
UPDATE yahoo_a_stock_eod_price SET symbol = UPPER(symbol)

UPDATE yahoo_link_table
INNER JOIN final_a_stock_eod_price ON final_a_stock_eod_price.symbol = yahoo_link_table.symbol and final_a_stock_eod_price.tradedate = yahoo_link_table.link_date 
INNER JOIN yahoo_a_stock_eod_price ON yahoo_a_stock_eod_price.symbol = yahoo_link_table.symbol AND yahoo_a_stock_eod_price.tradedate = yahoo_link_table.link_date
SET adj_ratio = ROUND(yahoo_a_stock_eod_price.adjclose, 2) / ROUND(final_a_stock_eod_price.adjclose, 2);

/* Check for missing symbol, only index data missing as expected */
select * from 
(select symbol from yahoo_link_table where adj_ratio is null) yahoo_missing_symbol
left join ts_link_table ON ts_link_table.w_symbol = yahoo_missing_symbol.symbol

/* Validate absolute OCHLV */
select 
final_a_stock_eod_price.*,
abs((yahoo_a_stock_eod_price.high / final_a_stock_eod_price.high) - 1) as high_diff,
abs((yahoo_a_stock_eod_price.low / final_a_stock_eod_price.low) - 1) as low_diff,
abs((yahoo_a_stock_eod_price.open / final_a_stock_eod_price.open) - 1) as open_diff,
abs((yahoo_a_stock_eod_price.close / final_a_stock_eod_price.close) - 1) as close_diff
from
yahoo_a_stock_eod_price
LEFT JOIN final_a_stock_eod_price ON  final_a_stock_eod_price.symbol = yahoo_a_stock_eod_price.symbol and final_a_stock_eod_price.tradedate = yahoo_a_stock_eod_price.tradedate
WHERE yahoo_a_stock_eod_price.close != 0 AND (high_diff > 0.01 OR low_diff > 0.01 OR open_diff > 0.01 OR close_diff > 0.01)
ORDER BY close_diff, open_diff, high_diff, low_diff desc

/* Validate adjclose */