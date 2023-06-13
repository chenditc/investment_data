## Why we need this
1. Stock price hit up limit or down limit price has special meaning in trading:
  - In backtest, we cannot trade after the price hits this number.
  - In feature design, this might means higher momentum.

## Initial import 
Related SQL stored in Procedure: https://www.dolthub.com/repositories/chenditc/investment_data/compare/master/l5e2000o8fd479n5dbqfufpkqegutq0k?tableName=dolt_procedures

1. Take all (tradedate, symbol, lag(close) as pre_close) from final_a_stock_eod_price table into final_a_stock_limit.
2. import tushare's daily limit data and override if data already exist in final_a_stock_limit.
3. Drop data earlier than "1996-12-196", as stop price is introduced after that.
4. Join final_a_stock_limit data with bao_a_stock_eod_info, fill the up / down limit based on if the stock is ST.
5. Correct precision problem by cross checking the high price and the up limit price. If the diff is less than 1%, set the up limit price to high price. If the diff is more than 1%, remove the row to represent there is no limit at that day.
6. Delete all rows with no preclose / uplimit /downlimit.

## Daily Update
1. On each day, update the data from tushare directly into final_a_stock_limit_data.

## Validation logic
1. final_a_stock_eod_price.high <= final_a_stock_limit.up_limit
2. final_a_stock_eod_price.high >= final_a_stock_limit.up_limit
3. daily count of final_a_stock_limit > 1000
