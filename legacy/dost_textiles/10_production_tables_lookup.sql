/* ============================================================================
   10_production_tables_lookup.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : 09_broader_prod_object_search.sql (run as DOSTEX) turned up real
             candidates that the exact-name guess in 07 missed:
               DOSTEX: PROD, WPRDTM, WPRDTD, WV_PROD_V (view),
                       WPRDTMD_V (view, currently INVALID - skip),
                       LOOMS, LOOMS_D, LOOM_SHEDS, LOOM_TYPES,
                       WV_LOOM_PICKRATE
               DOSTT:  PROD, PROD_M, PROD_D, WPRDTM, WPRDTD, LOOMS, LOOMS_D
             WPRDTM/WPRDTD and PROD_M/PROD_D look like master/detail table
             pairs - much more likely to hold real per-loom production rows
             than WEAVING_T (which turned out to be a voucher ledger).
             WV_PROD_V ("Weaving Production View") is the standout candidate
             for backing the Daily Production report screen.

   This script:
     Step 1 - full column list for every candidate table/view above
              (both DOSTEX and DOSTT owners, since 09 showed DOSTEX can see
              DOSTT's objects too).
     Step 2 - view SQL text for WV_PROD_V (reveals real base tables/join).
     Step 3 - row counts for the raw tables, so we know which pair
              (WPRDTM/WPRDTD vs PROD_M/PROD_D vs PROD) actually has data
              and roughly how much.
     Step 4 - LOOMS table columns confirmed separately in Step 1, but this
              adds a quick sample of loom master data (loom number/shed/
              type) so we can join production rows to it later.

   Target  : Oracle Database 11.2.0.1. Run as DOSTEX (confirmed it can see
             both schemas' objects).
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

PROMPT ============================================================
PROMPT STEP 1: Full column list for every candidate table/view
PROMPT ============================================================

SELECT owner, table_name, column_id, column_name, data_type
FROM   all_tab_columns
WHERE  table_name IN (
         'PROD','PROD_M','PROD_D','WPRDTM','WPRDTD','WV_PROD_V',
         'LOOMS','LOOMS_D','LOOM_SHEDS','LOOM_TYPES','WV_LOOM_PICKRATE'
       )
ORDER BY table_name, owner, column_id;

PROMPT ============================================================
PROMPT STEP 2: WV_PROD_V view text (reveals real base table(s)/join)
PROMPT ============================================================

SELECT owner, view_name, text
FROM   all_views
WHERE  view_name = 'WV_PROD_V';

PROMPT ============================================================
PROMPT STEP 3: Row counts for the raw candidate tables, both owners
PROMPT (tells us which pair actually holds production data)
PROMPT ============================================================

SELECT 'DOSTEX.PROD'    AS tbl, COUNT(*) AS row_count FROM dostex.prod
UNION ALL
SELECT 'DOSTEX.WPRDTM',          COUNT(*) FROM dostex.wprdtm
UNION ALL
SELECT 'DOSTEX.WPRDTD',          COUNT(*) FROM dostex.wprdtd
UNION ALL
SELECT 'DOSTT.PROD',             COUNT(*) FROM dostt.prod
UNION ALL
SELECT 'DOSTT.PROD_M',           COUNT(*) FROM dostt.prod_m
UNION ALL
SELECT 'DOSTT.PROD_D',           COUNT(*) FROM dostt.prod_d
UNION ALL
SELECT 'DOSTT.WPRDTM',           COUNT(*) FROM dostt.wprdtm
UNION ALL
SELECT 'DOSTT.WPRDTD',           COUNT(*) FROM dostt.wprdtd;

PROMPT ============================================================
PROMPT STEP 4: Loom master data sample (DOSTEX.LOOMS) - loom number/
PROMPT shed/type, to join production rows to once we find them
PROMPT ============================================================

SELECT * FROM dostex.looms WHERE ROWNUM <= 10;
