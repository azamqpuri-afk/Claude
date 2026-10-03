-- 33_store_issue_master_list.sql
-- ONE-TIME: build the frozen master list of distinct item codes issued via
-- Store Issue (DOSTEX.STORE_ISSUE_V, DT='ISS') in the trailing 2 months
-- (18-JUL-2026 through 18-SEP-2026, i.e. TRUNC(SYSDATE)-2 months to today).
-- This list is NOT regenerated automatically going forward - the weekly
-- stock-check automation (34_weekly_store_stock_check.py) reads this same
-- frozen CSV every Friday, it does not re-derive "issued in last 2 months"
-- each time. Re-run this script by hand only if the tracked item set needs
-- to be redefined later.
--
-- Dedup key = item code (IC) - one row per IC, no duplicates, per request.
-- UC (unit code) included since GET_ITEM_STOCK needs it and it's part of
-- this item's real key in the schema (I table PK is IC+UC).
SET LINESIZE 200
SET PAGESIZE 0
SET FEEDBACK OFF
SET HEADING ON
SET TRIMSPOOL ON
SET COLSEP ','
SPOOL store_issue_master_list.csv

SELECT '"IC","UC","IDESC","UOM","MIN_QTY","MAX_QTY","LOC","BIN","TOTAL_QTY_ISSUED_2MO","LAST_ISSUE_DATE"'
FROM dual;

SELECT '"' || s.ic || '","' || s.uc || '","' || REPLACE(i.idesc,'"','""') || '","' || i.uom
  || '","' || NVL(TO_CHAR(i.min_qty),'') || '","' || NVL(TO_CHAR(i.max_qty),'')
  || '","' || NVL(i.loc,'') || '","' || NVL(i.bin,'')
  || '","' || TO_CHAR(SUM(s.qty)) || '","' || TO_CHAR(MAX(s.dd),'DD-MON-YYYY') || '"'
FROM dostex.store_issue_v s
JOIN dostex.i i ON i.ic = s.ic AND i.uc = s.uc
WHERE s.dd >= ADD_MONTHS(TRUNC(SYSDATE), -2)
GROUP BY s.ic, s.uc, i.idesc, i.uom, i.min_qty, i.max_qty, i.loc, i.bin
ORDER BY 1;

SPOOL OFF
EXIT
