/* ============================================================================
   11_confirm_active_schema.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : WV_PROD_V (found in 10) only exists under DOSTEX, but DOSTT's
             WPRDTM/WPRDTD has ~3x the rows and a structurally different
             shape (own TD/TT/CONT_NO + full audit trail at the detail
             level, vs DOSTEX's normalized WPRDTM/CONTM/WYBEAMS joins) - so
             WV_PROD_V's join logic can't be reused as-is for DOSTT. Before
             deciding whether the daily report needs DOSTEX only, DOSTT
             only, or both combined, we need to know:
               (a) which schema(s) have RECENT (today/this week) production
                   rows - i.e. which is actually still being used day to day
               (b) what TT (transaction type) values exist in each schema's
                   WPRDTM, to confirm 'PRODUCTION' is the right filter for
                   both, not just DOSTEX
               (c) whether DOSTT has its own equivalent view we missed

   Target  : Oracle Database 11.2.0.1. Run as DOSTEX (confirmed visibility
             into both schemas).
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN schema_name FORMAT A14
COLUMN max_td       FORMAT A11
COLUMN max_feedd     FORMAT A11
COLUMN tt            FORMAT A20

PROMPT ============================================================
PROMPT STEP 1: Most recent transaction date (TD) and, where present,
PROMPT most recent system-feed date (FEEDD), per schema. Tells us
PROMPT which schema(s) are still being actively used TODAY.
PROMPT ============================================================

SELECT 'DOSTEX.WPRDTM' AS schema_name, COUNT(*) AS row_count,
       MAX(td) AS max_td, MAX(feedd) AS max_feedd
FROM   dostex.wprdtm
UNION ALL
SELECT 'DOSTT.WPRDTM', COUNT(*), MAX(td), MAX(feedd)
FROM   dostt.wprdtm
UNION ALL
SELECT 'DOSTT.WPRDTD', COUNT(*), MAX(prod_date), MAX(feedd)
FROM   dostt.wprdtd;

PROMPT ============================================================
PROMPT STEP 2: Distinct transaction types (TT) in WPRDTM, both
PROMPT schemas, with counts - confirms 'PRODUCTION' is the right
PROMPT filter value everywhere, and shows what else shares this
PROMPT table (issues, transfers, etc.)
PROMPT ============================================================

SELECT 'DOSTEX' AS schema_name, tt, COUNT(*) AS row_count
FROM   dostex.wprdtm
GROUP BY tt
UNION ALL
SELECT 'DOSTT', tt, COUNT(*)
FROM   dostt.wprdtm
GROUP BY tt
ORDER BY schema_name, row_count DESC;

PROMPT ============================================================
PROMPT STEP 3: Does DOSTT have its own production view (anything
PROMPT with PROD in the name, or referencing WPRDTM in its SQL text)
PROMPT that 09/10 might have missed?
PROMPT ============================================================

SELECT owner, view_name
FROM   all_views
WHERE  owner = 'DOSTT'
AND    (UPPER(view_name) LIKE '%PROD%' OR UPPER(text) LIKE '%WPRDTM%');

PROMPT ============================================================
PROMPT STEP 4: Last 5 production rows per schema (ordered by the
PROMPT most reliable recency column each has), to eyeball real
PROMPT current data - is DOSTEX stale/historical, or still live?
PROMPT ============================================================

SELECT * FROM (
    SELECT tno, td, tt, uc, feedd
    FROM   dostex.wprdtm
    WHERE  tt = 'PRODUCTION'
    ORDER BY tno DESC
) WHERE ROWNUM <= 5;

SELECT * FROM (
    SELECT tno, td, tt, uc, feedd
    FROM   dostt.wprdtm
    WHERE  tt = 'PRODUCTION'
    ORDER BY tno DESC
) WHERE ROWNUM <= 5;
