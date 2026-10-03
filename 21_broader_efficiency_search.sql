/* ============================================================================
   21_broader_efficiency_search.sql
   Project : Dost Textiles - Daily Loom Production Report
   Purpose : 20_loom_shift_efficiency_lookup.sql's exact-name guesses
             (WV_LOOM_EFF, WV_LOOM_WISE_EFF, WV_SHIFT_EFF, WV_SHIFT_WISE_
             EFF, WV_SHIFT_WISE_EFF_MAT - all pulled from the compiled
             .fmx forms' block-property strings) came back "no rows
             selected" on all 3 steps - same dead-end pattern as
             WV_DAILY_PRD earlier in this project. Same fix that worked
             then: widen to a wildcard search across ALL owners/object
             types instead of trusting the exact block-property names,
             which have repeatedly turned out not to match real DB object
             names in this ERP.

   Target  : Oracle Database 11.2.0.1. Run as DOSTEX.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN owner        FORMAT A14
COLUMN object_name  FORMAT A30
COLUMN object_type  FORMAT A12

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\broader_efficiency_search_output.txt

PROMPT ============================================================
PROMPT Connected as:
PROMPT ============================================================
SELECT USER AS connected_as FROM dual;

PROMPT ============================================================
PROMPT STEP 1: Wildcard search - anything (table/view/synonym/mview)
PROMPT whose name contains EFF, EFFICIENCY, LOOM_WISE, or SHIFT_WISE,
PROMPT visible to this login.
PROMPT ============================================================

SELECT owner, object_name, object_type, status
FROM   all_objects
WHERE  object_type IN ('TABLE','VIEW','SYNONYM','MATERIALIZED VIEW')
AND    (   UPPER(object_name) LIKE '%EFF%'
        OR UPPER(object_name) LIKE '%LOOM_WISE%'
        OR UPPER(object_name) LIKE '%SHIFT_WISE%'
       )
ORDER BY owner, object_type, object_name;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
