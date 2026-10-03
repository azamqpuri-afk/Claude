/* ============================================================================
   22_wv_eff_v_lookup.sql
   Project : Dost Textiles - Daily Loom Production Report
   Purpose : 21_broader_efficiency_search.sql found the real object -
             WV_EFF_V, a VIEW, VALID under DOSTEX and DOSTT (INVALID
             under DOSTT_GST - ignore that one). Naming matches WV_PROD_V
             exactly ("Weaving [Efficiency] View"). DOSTEX is the
             confirmed live schema (per 11_confirm_active_schema.sql), so
             DOSTEX.WV_EFF_V is the one that matters here.

   Step 1  : Full column list, DOSTEX.WV_EFF_V (DOSTT's too, for
             comparison only - that schema is dead since Oct-2023).
   Step 2  : View SQL text - reveals real base table(s)/join, same
             technique as WV_PROD_V in script 10.
   Step 3  : Raw unfiltered sample (top 10 rows) - real column values
             before assuming anything about the date column's name.
   Step 4  : Best-effort Aug 1-7 2026 pull, GUESSING the date column is
             called TD (matching WV_PROD_V's own convention - this view
             likely sources from the same WPRDTM/WPRDTD tables). Safe to
             fail (ORA-00904) if wrong - not a session-corrupting error,
             just tells us the real column name to use instead, visible
             in Step 1's output regardless.

   Target  : Oracle Database 11.2.0.1. Run as DOSTEX.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON
SET LONG 100000
SET LONGCHUNKSIZE 100000

COLUMN owner        FORMAT A12
COLUMN table_name   FORMAT A16
COLUMN column_name  FORMAT A28
COLUMN data_type    FORMAT A15

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\wv_eff_v_lookup_output.txt

PROMPT ============================================================
PROMPT STEP 1: Full column list for WV_EFF_V (DOSTEX + DOSTT)
PROMPT ============================================================

SELECT owner, table_name, column_id, column_name, data_type
FROM   all_tab_columns
WHERE  table_name = 'WV_EFF_V'
ORDER BY owner, column_id;

PROMPT ============================================================
PROMPT STEP 2: WV_EFF_V view text (DOSTEX) - real base table(s)/join
PROMPT ============================================================

SELECT owner, view_name, text
FROM   all_views
WHERE  view_name = 'WV_EFF_V'
AND    owner = 'DOSTEX';

PROMPT ============================================================
PROMPT STEP 3: Raw unfiltered sample (top 10 rows), DOSTEX.WV_EFF_V
PROMPT ============================================================

SELECT * FROM (
    SELECT * FROM dostex.wv_eff_v
) WHERE ROWNUM <= 10;

PROMPT ============================================================
PROMPT STEP 4: Best-effort Aug 1-7 2026 pull, guessing date column
PROMPT is TD (matching WV_PROD_V's convention) - safe to fail.
PROMPT ============================================================

SELECT *
FROM   dostex.wv_eff_v
WHERE  TRUNC(td) >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    TRUNC(td) <= TO_DATE('07-AUG-2026','DD-MON-YYYY')
ORDER BY td;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
