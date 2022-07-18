/* import completeness. Stock in ts should all present in final */
select ts_symbol_count.w_symbol, ts_symbol_count.cnt - final_symbol_count.cnt as cnt_diff 
from 
(
	select count(tradedate) as cnt, concat(substr(symbol, 8, 2), substr(symbol, 1, 6)) as w_symbol 
	from ts_a_stock_eod_price 
	group by symbol
) ts_symbol_count
LEFT JOIN 
(
  select count(tradedate) as cnt,  symbol 
	from final_a_stock_eod_price group by symbol
) final_symbol_count
ON ts_symbol_count.w_symbol = final_symbol_count.symbol


/* stock list completeness. Stock in stock list should at least have one price entry */



/* stock price correctness. Stock high low open close volume should match */

/* stock adjclose price should be close, we tolerate 5 cents difference in adj_close due to precision rounding issue */
