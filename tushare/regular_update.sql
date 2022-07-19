/* Add new stock to ts_link_table */
INSERT IGNORE INTO ts_link_table (w_symbol, link_symbol, link_date)
select concat(substr(symbol, 8, 2), substr(symbol, 1, 6)) as w_symbol, symbol as link_symbol, max(tradedate) as link_date 
from ts_a_stock_eod_price 
where tradedate = (select max(tradedate) from ts_a_stock_eod_price) group by symbol;

/* Fill in new stock price */
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
WHERE ts_a_stock_eod_price.symbol = missing_table.w_missing_symbol;

/* Set new stock adj ratio to 1 */
UPDATE ts_link_table  SET adj_ratio=1 WHERE adj_ratio is NULL;
