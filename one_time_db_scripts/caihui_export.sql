set echo off
set colsep ,
set underline off
set trimspool on
set trimout on
set termout off
set feedback off
set linesize 10000
set pagesize 0

spool caihui_stockprice.csv

select 
	REGEXP_REPLACE(TQ_QT_SKADJUSTQT.TRADEDATE, '([[:digit:]]{4})([[:digit:]]{2})([[:digit:]]{2})', '\1-\2-\3') || ',' ||
  CASE
	  WHEN TQ_QT_SKADJUSTQT.EXCHANGE='001002' THEN concat('SH', TQ_QT_SKADJUSTQT.symbol)
		WHEN TQ_QT_SKADJUSTQT.EXCHANGE='001003' THEN concat('SZ', TQ_QT_SKADJUSTQT.symbol)
	END || ',' ||
	TQ_QT_SKADJUSTQT.TOPEN || ',' ||
	TQ_QT_SKADJUSTQT.TCLOSE || ',' ||
	TQ_QT_SKADJUSTQT.THIGH || ',' ||
	TQ_QT_SKADJUSTQT.TLOW || ',' ||
	TQ_QT_SKADJUSTQT.ACLOADJUSTPRC || ',' ||
	TQ_QT_SKDAILYPRICE.AMOUNT || ',' ||
	TQ_QT_SKDAILYPRICE.VOL
from TQ_QT_SKADJUSTQT, TQ_QT_SKDAILYPRICE
WHERE TQ_QT_SKDAILYPRICE.SECODE = TQ_QT_SKADJUSTQT.SECODE
AND TQ_QT_SKDAILYPRICE.TRADEDATE = TQ_QT_SKADJUSTQT.TRADEDATE
AND TQ_QT_SKADJUSTQT.EXCHANGE IN ('001002', '001003');

spool off
