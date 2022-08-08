UPDATE final_a_stock_eod_price
INNER JOIN
	(select concat(substr(symbol, 8, 2), substr(symbol, 1, 6)) as symbol , tradedate, amount from ts_a_stock_eod_price) ts
	ON final_a_stock_eod_price.symbol = ts.symbol AND final_a_stock_eod_price.tradedate = ts.tradedate
SET final_a_stock_eod_price.amount = ts.amount