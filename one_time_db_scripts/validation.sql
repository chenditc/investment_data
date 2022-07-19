CREATE TABLE `c_link_table` (
  `symbol` varchar(100) NOT NULL,
  `link_date` date NOT NULL,
  `adj_ratio` double,
  PRIMARY KEY (`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin

/* Populate c_link_table */
INSERT INTO c_link_table (symbol, link_date)
select symbol, max(tradedate) as link_date 
from c_a_stock_eod_price
WHERE close != 0
group by symbol;

UPDATE c_link_table
INNER JOIN final_a_stock_eod_price ON final_a_stock_eod_price.symbol = c_link_table.symbol and final_a_stock_eod_price.tradedate = c_link_table.link_date 
INNER JOIN c_a_stock_eod_price ON c_a_stock_eod_price.symbol = c_link_table.symbol AND c_a_stock_eod_price.tradedate = c_link_table.link_date
SET adj_ratio = ROUND(c_a_stock_eod_price.adjclose, 2) / ROUND(final_a_stock_eod_price.adjclose, 2)

/* Fill in A stock, ignore B stock */
INSERT IGNORE INTO final_a_stock_eod_price (tradedate, symbol, high, low, open, close, volume, adjclose) 
select c_a_stock_eod_price.*
FROM c_a_stock_eod_price, 
 (select symbol from c_link_table where adj_ratio is NULL AND symbol not like 'SH9%' AND symbol not like 'SZ2%') missing_symbol
 WHERE c_a_stock_eod_price.symbol = missing_symbol.symbol AND c_a_stock_eod_price.CLOSE != 0

/* Validate absolute OCHLV */
select 
final_a_stock_eod_price.*,
abs((c_a_stock_eod_price.high / final_a_stock_eod_price.high) - 1) as high_diff,
abs((c_a_stock_eod_price.low / final_a_stock_eod_price.low) - 1) as low_diff,
abs((c_a_stock_eod_price.open / final_a_stock_eod_price.open) - 1) as open_diff,
abs((c_a_stock_eod_price.close / final_a_stock_eod_price.close) - 1) as close_diff
from
c_a_stock_eod_price
LEFT JOIN final_a_stock_eod_price ON  final_a_stock_eod_price.symbol = c_a_stock_eod_price.symbol and final_a_stock_eod_price.tradedate = c_a_stock_eod_price.tradedate
WHERE c_a_stock_eod_price.close != 0 AND (high_diff > 0.01 OR low_diff > 0.01 OR open_diff > 0.01 OR close_diff > 0.01)
ORDER BY close_diff, open_diff, high_diff, low_diff desc

/* Validate adjclose */