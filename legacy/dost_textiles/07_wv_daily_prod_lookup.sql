/* ============================================================================
   07_wv_daily_prod_lookup.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : WEAVING_T (see 05/06) turned out to be a voucher ledger, not a
             production log. Found the real screen instead - ISOFT Weaving
             Management System's Reports > Production > Daily Production
             (date + From/To loom range). Pulled strings out of the compiled
             form (D:\DOST_TEX\WEAVING\FORMS\WV_DAILY_PROD.fmx and
             WV_LOOM_WISE_PRD.fmx) and found these likely view/table names
             tied to its data blocks (PB_DET/PB_LOOM/PB_SUM):
               WV_DAILY_PRD, WV_DAILY_PRD_SUM, WV_LOOM_PRD, WV_LOOM_PRD_EFF,
               WV_LOOM_BEAM_WISE_PRD, WV_LOOM_WISE_PRD
             This script confirms which of these actually exist in the DB,
             whether each is a TABLE or VIEW, lists its columns, and - for
             views - pulls the view's SQL text so we can see the real base
             table(s) underneath if the view itself is just a join/wrapper.

   Target  : Oracle Database 11.2.0.1. Run as DOSTT (or DOSTEX if DOSTT
             comes back empty for any of these - try both schemas' grants).
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON
SET LONG 100000
SET LONGCHUNKSIZE 100000

COLUMN owner        FORMAT A14
COLUMN object_name  FORMAT A28
COLUMN object_type  FORMAT A12
COLUMN column_name  FORMAT A30
COLUMN data_type    FORMAT A15

PROMPT ============================================================
PROMPT STEP 1: Which of these candidate names actually exist, in
PROMPT which schema(s), and are they a TABLE or a VIEW?
PROMPT ============================================================

SELECT owner, object_name, object_type, status
FROM   all_objects
WHERE  object_name IN (
         'WV_DAILY_PRD','WV_DAILY_PRD_SUM','WV_LOOM_PRD','WV_LOOM_PRD_EFF',
         'WV_LOOM_BEAM_WISE_PRD','WV_LOOM_WISE_PRD'
       )
ORDER BY object_name, owner;

PROMPT ============================================================
PROMPT STEP 2: Full column list for every match above (works for
PROMPT both tables and views - views show their exposed columns too)
PROMPT ============================================================

SELECT owner, table_name AS object_name, column_id, column_name, data_type
FROM   all_tab_columns
WHERE  table_name IN (
         'WV_DAILY_PRD','WV_DAILY_PRD_SUM','WV_LOOM_PRD','WV_LOOM_PRD_EFF',
         'WV_LOOM_BEAM_WISE_PRD','WV_LOOM_WISE_PRD'
       )
ORDER BY table_name, owner, column_id;

PROMPT ============================================================
PROMPT STEP 3: For any of these that are VIEWS, show the view's SQL
PROMPT text - reveals the real base table(s)/join underneath.
PROMPT ============================================================

SELECT owner, view_name, text
FROM   all_views
WHERE  view_name IN (
         'WV_DAILY_PRD','WV_DAILY_PRD_SUM','WV_LOOM_PRD','WV_LOOM_PRD_EFF',
         'WV_LOOM_BEAM_WISE_PRD','WV_LOOM_WISE_PRD'
       )
ORDER BY view_name, owner;
