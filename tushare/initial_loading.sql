/* Populate ts_link_table */
INSERT INTO ts_link_table (w_symbol, link_symbol, link_date)
select concat(substr(symbol, 8, 2), substr(symbol, 1, 6)) as w_symbol, symbol as link_symbol, max(tradedate) as link_date 
from ts_a_stock_eod_price where tradedate < "2019-01-01" group by symbol;

/* Calculate adj_ratio, round to prevent floating point issue */
UPDATE ts_link_table
INNER JOIN w_a_stock_eod_price ON w_a_stock_eod_price.symbol = ts_link_table.w_symbol and w_a_stock_eod_price.tradedate = ts_link_table.link_date 
INNER JOIN ts_a_stock_eod_price ON ts_a_stock_eod_price.symbol = ts_link_table.link_symbol AND ts_a_stock_eod_price.tradedate = ts_link_table.link_date
SET adj_ratio = ROUND(ts_a_stock_eod_price.adjclose, 2) / ROUND(w_a_stock_eod_price.adjclose, 2)

/* Fill in stock where w stock does not exists */
INSERT IGNORE INTO final_a_stock_eod_price (tradedate, symbol, high, low, open, close, volume, adjclose)
select ts_a_stock_eod_price.tradedate, 
			missing_table.w_symbol as symbol,
			ts_a_stock_eod_price.high,
			ts_a_stock_eod_price.low,
			ts_a_stock_eod_price.open,
			ts_a_stock_eod_price.close,
			ts_a_stock_eod_price.volume,
			ROUND(ts_a_stock_eod_price.adjclose, 2)
FROM ts_a_stock_eod_price, 
	(
		select distinct(link_symbol) as w_missing_symbol, w_symbol from ts_link_table 
		WHERE adj_ratio is NULL
	) missing_table
WHERE ts_a_stock_eod_price.symbol = missing_table.w_missing_symbol

UPDATE ts_link_table  SET adj_ratio=1 WHERE adj_ratio is NULL

/* Fill in rest of stock data from ts to final */
INSERT IGNORE INTO final_a_stock_eod_price (tradedate, symbol, high, low, open, close, volume, adjclose) 
select ts_a_stock_eod_price.tradedate, 
			ts_link_table.w_symbol as symbol,
			ts_a_stock_eod_price.high,
			ts_a_stock_eod_price.low,
			ts_a_stock_eod_price.open,
			ts_a_stock_eod_price.close,
			ts_a_stock_eod_price.volume,
			ROUND(ts_a_stock_eod_price.adjclose / ts_link_table.adj_ratio, 2) as adjclose from ts_a_stock_eod_price
LEFT JOIN ts_link_table ON ts_a_stock_eod_price.symbol = ts_link_table.link_symbol
where ts_a_stock_eod_price.tradedate > "2019-01-01"

/* Add rest all stock list and price entry */
INSERT IGNORE INTO ts_link_table (w_symbol, link_symbol, link_date, adj_ratio)
select concat(substr(symbol, 8, 2), substr(symbol, 1, 6)) as w_symbol, symbol as link_symbol, max(tradedate) as link_date, 1 as adj_ratio
from ts_a_stock_eod_price 
where tradedate > "2019-01-01" group by symbol;

INSERT IGNORE INTO final_a_stock_eod_price (tradedate, symbol, high, low, open, close, volume, adjclose) 
select ts_a_stock_eod_price.tradedate, 
			ts_link_table.w_symbol as symbol,
			ts_a_stock_eod_price.high,
			ts_a_stock_eod_price.low,
			ts_a_stock_eod_price.open,
			ts_a_stock_eod_price.close,
			ts_a_stock_eod_price.volume,
			ROUND(ts_a_stock_eod_price.adjclose / ts_link_table.adj_ratio, 2) as adjclose 
FROM ts_a_stock_eod_price
LEFT JOIN ts_link_table ON ts_a_stock_eod_price.symbol = ts_link_table.link_symbol