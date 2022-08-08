set echo off
set colsep ,
set underline off
set trimspool on
set trimout on
set termout off
set feedback off
set linesize 10000
set pagesize 0

spool wind_stockprice.csv

select REGEXP_REPLACE(TRADE_DT, '([[:digit:]]{4})([[:digit:]]{2})([[:digit:]]{2})', '\1-\2-\3') || ',' || concat(SUBSTR(S_INFO_WINDCODE, 8, 9), SUBSTR(S_INFO_WINDCODE, 0, 6))  || ',' || S_DQ_HIGH || ',' || S_DQ_LOW || ',' || S_DQ_OPEN|| ',' || S_DQ_CLOSE || ',' ||S_DQ_ADJCLOSE  || ',' || S_DQ_VOLUME  || ',' ||  S_DQ_AMOUNT from ASHAREEODPRICES;

spool off
