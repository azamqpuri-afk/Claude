/* ============================================================================
   14_loom_quality_history_graded.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : Same export as 13_loom_quality_history.sql, extended with
             grade-wise meters. DOSTEX.WV_PROD_V's own definition (see
             10_production_tables_lookup.sql's Step 2 output) confirms the
             exact column names - they're already exposed on the view
             itself, no need to reach back to the base table (WPRDTD):

               D.A, D.A2, D.B, D.C, D.CP,
               ((NVL(D.A,0)+NVL(D.A2,0)+NVL(D.B,0)+NVL(D.C,0)+NVL(D.CP,0)))
                 AS TOTAL_MTR

             i.e. TOTAL_MTR = A + A2 + B + C + CP exactly, confirming the
             business definitions given for this script:
               PRODUCTION (first-quality) = A + A2
               REJECTION  (lower/reject)  = B + C + CP
             Added PRODUCTION_MTR and REJECTION_MTR as two extra computed
             CSV columns (not explicitly requested, but directly implied by
             the formulas given - cheap to add here vs. recomputing
             downstream from the 5 raw grade columns every time; drop them
             from the SELECT list below if not wanted).

   Columns : TD, LOOM_NAME, QUALITY, A, A2, B, C, CP, PRODUCTION_MTR,
             REJECTION_MTR, TOTAL_MTR, TOTAL_FAULTS, SHIFT - comma-separated,
             grade breakdown placed between QUALITY and TOTAL_MTR since it
             leads into the total naturally.

   Everything else (date range, entry-lag caveat, CSV quoting/escaping
   rules, self-contained SPOOL/SPOOL OFF) is unchanged from script 13 -
   see that script's header for the full rationale on each of those.
   ============================================================================ */

SET PAGESIZE 0
SET FEEDBACK OFF
SET HEADING OFF
SET LINESIZE 500
SET WRAP OFF
SET TRIMSPOOL ON
SET VERIFY OFF
SET ECHO OFF

COLUMN csv_row FORMAT A500

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\loom_quality_history_graded_output.csv

PROMPT TD,LOOM_NAME,QUALITY,A,A2,B,C,CP,PRODUCTION_MTR,REJECTION_MTR,TOTAL_MTR,TOTAL_FAULTS,SHIFT

SELECT TO_CHAR(v.td,'YYYY-MM-DD') || ',' ||
       '"' || REPLACE(lm.lname, '"', '""') || '"' || ',' ||
       '"' || REPLACE(v.quality, '"', '""') || '"' || ',' ||
       TO_CHAR(NVL(v.a,0),  'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.a2,0), 'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.b,0),  'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.c,0),  'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.cp,0), 'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.a,0) + NVL(v.a2,0), 'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.b,0) + NVL(v.c,0) + NVL(v.cp,0), 'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.total_mtr,0), 'FM999999990.00') || ',' ||
       TO_CHAR(NVL(v.total_faults,0), 'FM999999990') || ',' ||
       v.shift AS csv_row
FROM   dostex.wv_prod_v v
JOIN   dostex.looms lm ON lm.lcode = v.lcode
-- date range: 01-AUG-2026 through today (today likely empty - entry lag)
WHERE  TRUNC(v.td) >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    TRUNC(v.td) <= TRUNC(SYSDATE)
ORDER BY v.td, lm.lcode
;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
SET HEADING ON
SET WRAP ON
