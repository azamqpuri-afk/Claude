/* ============================================================================
   20_loom_shift_efficiency_lookup.sql
   Project : Dost Textiles - Daily Loom Production Report
   Purpose : Found the real screen: ERP UI Reports -> Production -> Loom
             Wise Efficiency / Shift Wise Efficiency (Weaving Management
             System). Same string-extraction technique used for WV_DAILY_
             PROD earlier - read D:\DOST_TEX\WEAVING\FORMS\WV_LOOM_EFF.fmx
             and WV_SHIFT_EFF.fmx as raw bytes, regex out printable runs,
             filter to identifier-shaped strings. Both have the literal
             "From Date"/"From Loom" prompt text (confirms these are the
             right forms) and point to:
               WV_LOOM_EFF.fmx  -> WV_LOOM_EFF, WV_LOOM_WISE_EFF
               WV_SHIFT_EFF.fmx -> WV_SHIFT_EFF, WV_SHIFT_WISE_EFF,
                                    WV_SHIFT_WISE_EFF_MAT

   This script checks which of these 5 names actually exist in the DB,
   what they are (table/view), their columns, view text if applicable,
   and - for whatever turns out real - an UNFILTERED raw sample (top 20
   rows) so we can see real column values including whatever the date
   column is actually called, before writing a properly-filtered Aug 1-7
   2026 pull as a fast follow-up once we know real names.

   If NONE of these 5 names exist anywhere, or everything found is a
   VIEW with no real underlying table (i.e. it's built from other already-
   known tables rather than storing efficiency numbers itself), or a
   report exists in the UI with no backing table at all reachable this
   way - that itself is the answer requested: efficiency stays a manual/
   UI-calculated figure, not something queryable directly, and that's
   worth stating plainly rather than forcing a guess.

   Target  : Oracle Database 11.2.0.1. Run as DOSTEX (can see DOSTT's
             objects too, per earlier confirmed cross-schema grants).
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON
SET LONG 100000
SET LONGCHUNKSIZE 100000

COLUMN owner        FORMAT A12
COLUMN object_name  FORMAT A24
COLUMN object_type  FORMAT A12
COLUMN table_name   FORMAT A20
COLUMN column_name  FORMAT A28
COLUMN data_type    FORMAT A15

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\loom_shift_efficiency_lookup_output.txt

PROMPT ============================================================
PROMPT STEP 1: Which of the 5 candidate names actually exist, in
PROMPT which schema(s), table or view?
PROMPT ============================================================

SELECT owner, object_name, object_type, status
FROM   all_objects
WHERE  object_name IN (
         'WV_LOOM_EFF','WV_LOOM_WISE_EFF',
         'WV_SHIFT_EFF','WV_SHIFT_WISE_EFF','WV_SHIFT_WISE_EFF_MAT'
       )
ORDER BY object_name, owner;

PROMPT ============================================================
PROMPT STEP 2: Full column list for every match above.
PROMPT ============================================================

SELECT owner, table_name, column_id, column_name, data_type
FROM   all_tab_columns
WHERE  table_name IN (
         'WV_LOOM_EFF','WV_LOOM_WISE_EFF',
         'WV_SHIFT_EFF','WV_SHIFT_WISE_EFF','WV_SHIFT_WISE_EFF_MAT'
       )
ORDER BY table_name, owner, column_id;

PROMPT ============================================================
PROMPT STEP 3: View SQL text for any of the above that are VIEWS -
PROMPT reveals real base tables if these are just wrappers.
PROMPT ============================================================

SELECT owner, view_name, text
FROM   all_views
WHERE  view_name IN (
         'WV_LOOM_EFF','WV_LOOM_WISE_EFF',
         'WV_SHIFT_EFF','WV_SHIFT_WISE_EFF','WV_SHIFT_WISE_EFF_MAT'
       );

PROMPT ============================================================
PROMPT STEP 4: Raw unfiltered sample (top 10 rows) of each match
PROMPT that's a real table or view - manually add one query per
PROMPT name Step 1 confirms exists (can't template this blind since
PROMPT we don't yet know which of the 5 names are real). Example
PROMPT pattern once you know a real name, e.g. WV_LOOM_EFF:
PROMPT   SELECT * FROM (SELECT * FROM dostex.wv_loom_eff ORDER BY 1 DESC)
PROMPT   WHERE ROWNUM <= 10;
PROMPT (repeat per confirmed real object - left as a template since
PROMPT Step 1's result determines what's actually queryable here)
PROMPT ============================================================

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
