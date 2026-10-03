/* ============================================================================
   24_po_main_backdate.sql  (2026-08-14)
   Project : Dost Textiles - ERP Backdate Detection
   Purpose : Financial backdate-detection query against DOSTEX.PO_MAIN
             (purchase orders) - same audit-column-vs-document-date method
             that worked for EMP_LOANS (02_emp_loans_backdate.sql), applied
             here for the first time. Unlike WPRDTM (production - see
             25_wprdtm_tno_sequence_check.sql), PO_MAIN.FEEDD is a real,
             usable signal: confirmed 100% populated (1270/1270 rows) via
             direct query before trusting it (same discipline as every
             other audit-column check in this project - never assume).

   Logic   : GAP_DAYS = TRUNC(FEEDD) - PO_DATE
               large positive -> PO_DATE predates the real entry: backdating
               negative       -> none observed (see Step B: FUTURE_DATED=0)

   Findings (2026-08-14, this run's numbers - re-run to refresh):
     - 1270 total rows, FEEDD 100% populated, 0 future-dated.
     - 53 rows backdated >3 days, 21 >7 days, 1 extreme (>30 days) outlier.
     - Two distinct clusters, different likely explanations:
         1. Oct-Nov 2023 (PO_NO 1-16ish, entered in batches on 19/20/21/28-
            Oct and 10-Nov-2023): matches this ERP's own confirmed go-live
            window (DOSTT retired schema's own MAX(TD)=30-SEP-23 -> DOSTEX
            cutover, see 11_confirm_active_schema.sql) - same "backlog
            clearing" shape as the EMP_LOANS Jan-2024 finding. Likely a
            migration artifact, not fraud - but not confirmed with the
            business yet, same caveat as EMP_LOANS.
         2. Small clusters well AFTER go-live (Apr/May-2025, Jan/Mar-2026),
            4-8 day lag, all under the shared ADMIN login, entered right
            after weekend/holiday-adjacent dates - looks like routine
            catch-up, lower severity, but still shared-login so no
            individual accountability either way.
     - ONE STANDOUT, genuinely unexplained by either pattern above:
       PO_NO 1242, PO_DATE 2023-07-13 (predates even the Oct-2023 go-live),
       FEEDD 2026-07-14 (entered essentially last month) - a 1097-day gap.
       PURCHASER = MIAN AZAM. Worth asking about directly - this is the one
       row here that doesn't have an obvious innocent explanation.

   Caveat  : Same as EMP_LOANS - FEEDD is the best available proxy for
             "system timestamp" in this ERP, not independently verified as
             non-editable from the Forms UI. Treat flagged rows as leads.

   Tuning  : BACKDATE_THRESHOLD_DAYS (currently 3) appears in the `flagged`
             CTE - lower than EMP_LOANS' 7-day threshold since PO_MAIN's
             AVG_LAG_DAYS is only 2.5 (a much tighter normal-case baseline
             than payroll), so 3 days already separates signal from noise.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\po_main_backdate_output.txt

PROMPT ============================================================
PROMPT STEP A: FEEDD population + overall lag distribution - confirm
PROMPT the audit column is usable before trusting it.
PROMPT ============================================================

SELECT
    COUNT(*)                                                  AS total_rows,
    COUNT(feedd)                                              AS has_feedd,
    COUNT(CASE WHEN TRUNC(feedd) - po_date > 3  THEN 1 END)   AS backdated_gt3,
    COUNT(CASE WHEN TRUNC(feedd) - po_date > 7  THEN 1 END)   AS backdated_gt7,
    COUNT(CASE WHEN TRUNC(feedd) - po_date > 30 THEN 1 END)   AS backdated_gt30,
    COUNT(CASE WHEN TRUNC(feedd) - po_date < 0  THEN 1 END)   AS future_dated,
    MAX(TRUNC(feedd) - po_date)                               AS worst_case_days,
    ROUND(AVG(TRUNC(feedd) - po_date), 1)                     AS avg_lag_days
FROM dostex.po_main;

PROMPT ============================================================
PROMPT STEP B: Batch view - entry date (FEEDD) grouped, restricted to
PROMPT days where at least one backdated PO was entered. Same shape
PROMPT check as EMP_LOANS' admin_batch analysis (100%-backdated batch
PROMPT = likely single-event backlog clearing, not scattered fraud).
PROMPT ============================================================

COLUMN entry_date  FORMAT A11

SELECT TRUNC(feedd)                                          AS entry_date,
       COUNT(*)                                               AS rows_entered,
       COUNT(CASE WHEN TRUNC(feedd) - po_date > 3 THEN 1 END) AS backdated_in_batch,
       MIN(po_date)                                            AS earliest_po_date,
       MAX(po_date)                                            AS latest_po_date
FROM   dostex.po_main
GROUP BY TRUNC(feedd)
HAVING COUNT(CASE WHEN TRUNC(feedd) - po_date > 3 THEN 1 END) > 0
ORDER BY entry_date;

PROMPT ============================================================
PROMPT STEP C: Row-level detail for every backdated PO (threshold 3
PROMPT days), worst gap first - the actual flagged list to review.
PROMPT ============================================================

COLUMN po_date    FORMAT A11
COLUMN feedd      FORMAT A20
COLUMN usern      FORMAT A10
COLUMN purchaser  FORMAT A16
COLUMN gap_days   FORMAT 99999

WITH flagged AS (
    SELECT po_no, po_date,
           TO_CHAR(feedd,'DD-MON-YYYY HH24:MI:SS') AS feedd,
           (TRUNC(feedd) - po_date) AS gap_days,
           usern, purchaser
    FROM   dostex.po_main
    WHERE  TRUNC(feedd) - po_date > 3   -- <-- BACKDATE_THRESHOLD_DAYS, edit here
)
SELECT * FROM flagged
ORDER BY gap_days DESC;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
