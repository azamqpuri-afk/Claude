/* ============================================================================
   16_loom_quality_history_with_contract.sql  (v3 - 2026-08-08)
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : Same export as 14_loom_quality_history_graded.sql, extended
             with contract number, quality's pick, and per-row Net Sale.

   v3 changelog - v2 chased a nonexistent problem. It looked for a
   separate "actual pick" on WPRDTD (joined via TRANS_NO/SR_NO), on the
   theory that a contract's PICK was just an opening baseline that could
   differ from what a specific batch actually ran at. That WPRDTD join
   came back NULL on every row - and 18_wprdtd_rate_pick_check.sql then
   confirmed WPRDTD.PICK and WPRDTD.RATE are BOTH completely unpopulated
   across the entire table (0 of 28,096 rows, not just this window) - two
   dead columns, not a data source.

   Azam's clarification resolves this properly: PICK is a property of the
   QUALITY itself (it's part of the weave construction - e.g. quality
   "76X66..." literally has PICK=66 embedded in its own code), not
   something separately recorded per production transaction. "All
   qualities are entered according to picks in the production" - i.e.
   every production row is already correctly tagged to the right
   quality/CONT_SUB_ID, so the PICK that WV_PROD_V already exposes
   (sourced from WC_CONT_SUB_V via each row's own CONT_SUB_ID - see
   10_production_tables_lookup.sql's view text) is already the right
   per-row value. No separate WPRDTD lookup needed - v2's WPRDTD join
   dropped entirely.

   Net Sale (rate/meter) = PICK x contract's PER_PICK_RATE, computed per
   row (a contract can have multiple quality sub-lines at different picks
   - this multiplies each row by ITS OWN quality's pick, not one fixed
   value per contract). PER_PICK_RATE still comes from CONTM (header) -
   confirmed real source per 15_contract_rates.sql v4 / 17_contract_rate_
   diagnosis.sql (WC_CONT_SUB_V's own PER_PICK_RATE column came back NULL
   on every row checked).

   Note: CONT_NO=66's earlier flagged mismatch (0.76 x 76 = 57.76 expected
   vs 53.20 stored) is NOT explained by this clarification - all of that
   contract's Aug 1-6 rows share the same PICK=76 (one quality/CONT_SUB_ID
   throughout this window), so the formula should hold and doesn't.
   Still an open, real anomaly - flag it to Azam separately (alongside
   that contract's ~55x outlier FABRIC_RATE) rather than assuming it's
   resolved.

   Columns : TD, LOOM_NAME, QUALITY, CONT_NO, PICK, PER_PICK_RATE,
             NET_SALE, NET_SALE_WITH_GST, A, A2, B, C, CP, PRODUCTION_MTR,
             REJECTION_MTR, TOTAL_MTR, TOTAL_FAULTS, SHIFT

   NET_SALE is a PRE-GST rate/meter (matches PER_PICK_RATE's own units -
   see 15_contract_rates.sql v4's finding that CONVER_PER_METER is
   GST-inclusive) - NOT a total money amount, and NOT yet multiplied by
   meters produced. NET_SALE_WITH_GST is a bonus column, same pattern as
   script 15's GST handling. Both will be NULL for CONT_NO 46/60/67/68
   (confirmed genuinely missing rate data, not a bug).

   Everything else (date range, entry-lag caveat, CSV quoting/escaping
   rules, self-contained SPOOL/SPOOL OFF) unchanged from scripts 13/14 -
   see script 13's header for the full rationale on each of those.
   ============================================================================ */

SET PAGESIZE 0
SET FEEDBACK OFF
SET HEADING OFF
SET LINESIZE 600
SET WRAP OFF
SET TRIMSPOOL ON
SET VERIFY OFF
SET ECHO OFF

COLUMN csv_row FORMAT A600

SPOOL 'O:\My Drive\DostTextile_Reports\loom_quality_history_with_contract_output.csv'

PROMPT TD,LOOM_NAME,QUALITY,CONT_NO,PICK,PER_PICK_RATE,NET_SALE,NET_SALE_WITH_GST,A,A2,B,C,CP,PRODUCTION_MTR,REJECTION_MTR,TOTAL_MTR,TOTAL_FAULTS,SHIFT

SELECT TO_CHAR(v.td,'YYYY-MM-DD') || ',' ||
       '"' || REPLACE(lm.lname, '"', '""') || '"' || ',' ||
       '"' || REPLACE(v.quality, '"', '""') || '"' || ',' ||
       '"' || REPLACE(v.cont_no, '"', '""') || '"' || ',' ||
       TO_CHAR(v.pick) || ',' ||
       TO_CHAR(c.per_pick_rate, 'FM990.00') || ',' ||
       TO_CHAR(ROUND(v.pick * c.per_pick_rate, 2), 'FM999990.00') || ',' ||
       TO_CHAR(ROUND(v.pick * c.per_pick_rate * (1 + NVL(c.gst_per,0)/100), 2), 'FM999990.00') || ',' ||
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
LEFT JOIN dostex.contm c ON c.cont_id = v.cont_id
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
