/* ============================================================================
   25_wprdtm_tno_sequence_check.sql  (2026-08-14)
   Project : Dost Textiles - ERP Backdate Detection
   Purpose : Production backdate-detection query against DOSTEX.WPRDTM
             (TT='PRODUCTION'). This CANNOT use the standard audit-column-
             vs-document-date method (02_emp_loans_backdate.sql,
             24_po_main_backdate.sql) because WPRDTM has NO usable audit
             trail at all - FEEDD/USERN/IPADD are confirmed 100% NULL
             across the table's entire history (all TT values, 2023-2026 -
             see dost-textiles-production-automation memory / README's
             19_entry_timing_pattern.sql section), and ORA_ROWSCN is also
             unusable (WPRDTM has ROWDEPENDENCIES DISABLED, so the SCN is
             block-level and contaminated by unrelated rows sharing the
             same physical block - confirmed by hand, see the same memory
             note). Both dead ends checked and ruled out BEFORE building
             this - not assumed.

   Fallback method: DOSTEX.WPRDTM.TNO. Confirmed empirically (this script's
             own Step A) to be a clean, gapless, strictly-increasing
             integer sequence (1 to row-count, no duplicates, no gaps) -
             consistent with an Oracle SEQUENCE.NEXTVAL assigned at INSERT
             time, i.e. a true insertion-order proxy even though it carries
             no timestamp. TD (the claimed production date) should be
             non-decreasing as TNO increases, for daily entry in normal
             chronological order; a case where TD DECREASES as TNO
             increases means a later-inserted row was given an EARLIER
             claimed date than the row inserted just before it - the same
             underlying signal a timestamp-based check would catch, just
             without hour-level precision (matches the earlier "date, not
             exact time" resolution already established as the ceiling for
             this table).

   Findings (2026-08-14, this run's numbers - re-run to refresh):
     - 1023 rows, TT='PRODUCTION', TNO spans 1 (2023-10-01, the ERP's own
       go-live date) to 1023 (2026-08-13) with zero gaps/duplicates -
       confirms the sequence assumption before relying on it.
     - Only 4 cases where TD decreases as TNO increases, out of 1023 rows
       across ~3 years - three are single-day swaps (adjacent days entered
       in reverse order), one is a 6-day swap (TNO 948->949: TD 31-May-26
       -> 25-May-26). No batch pattern, no clustering, no shared-login
       spike like PO_MAIN/EMP_LOANS showed.
     - READ THIS AS A CLEAN RESULT: production data entry looks orderly -
       this is the useful finding here, not a list of suspects. Contrast
       with 24_po_main_backdate.sql's real batches - production doesn't
       show the same pattern.
     - Separately (not backdating, a different signal): TD deltas of 5-7
       days between consecutive TNOs exist (9 outlier deltas total,
       mixing the small backward swaps above with forward gaps) - forward
       gaps mean several days went by with no production entered at all,
       i.e. a data-entry/production gap, not backdating. Cross-reference
       against the daily automation's own trailing-gap log
       (production_report_status_log.txt) if the specific gap dates matter.

   Caveat  : TNO's insertion-order property is inferred from its observed
             shape (gapless, strictly increasing, starts at the confirmed
             go-live date) - not confirmed against the Forms application
             source the way FEEDD's role was originally documented. Treat
             flagged rows as leads, same caveat as every other check here.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\wprdtm_tno_sequence_output.txt

PROMPT ============================================================
PROMPT STEP A: Confirm TNO is actually a clean insertion-order
PROMPT sequence before trusting it as an audit-column substitute:
PROMPT gapless, no duplicates, spans go-live to most-recent entry.
PROMPT ============================================================

SELECT COUNT(*)              AS total_rows,
       COUNT(DISTINCT tno)   AS distinct_tno,
       MIN(tno)              AS min_tno,
       MAX(tno)              AS max_tno,
       MIN(td)               AS min_td,
       MAX(td)               AS max_td
FROM   dostex.wprdtm
WHERE  tt = 'PRODUCTION';

PROMPT ============================================================
PROMPT STEP B: Ordering anomalies - rows where TD does NOT increase
PROMPT (or actively decreases) as TNO increases by 1. Each row here
PROMPT means the later-inserted entry (higher TNO) claims an EARLIER
PROMPT production date than the entry right before it.
PROMPT ============================================================

COLUMN prev_td  FORMAT A11
COLUMN td       FORMAT A11
COLUMN td_delta_days FORMAT 99999

WITH ordered AS (
    SELECT tno, td,
           LAG(tno) OVER (ORDER BY tno) AS prev_tno,
           LAG(td)  OVER (ORDER BY tno) AS prev_td
    FROM   dostex.wprdtm
    WHERE  tt = 'PRODUCTION'
)
SELECT prev_tno, prev_td, tno, td,
       (td - prev_td) AS td_delta_days
FROM   ordered
WHERE  prev_tno IS NOT NULL
AND    tno = prev_tno + 1          -- consecutive TNOs only (no gaps expected anyway)
AND    td <= prev_td               -- non-increasing = anomaly
ORDER BY tno;

PROMPT ============================================================
PROMPT STEP C: Full TD-delta distribution per +1 TNO step - the
PROMPT outliers here are either backdating (delta <= 0, see Step B)
PROMPT or entry gaps (delta >= 5, several days with nothing entered:
PROMPT a completeness signal, not a backdating one).
PROMPT ============================================================

COLUMN td_delta_days FORMAT 99999
COLUMN row_count     FORMAT 9999

WITH ordered AS (
    SELECT tno, td,
           LAG(tno) OVER (ORDER BY tno) AS prev_tno,
           LAG(td)  OVER (ORDER BY tno) AS prev_td
    FROM   dostex.wprdtm
    WHERE  tt = 'PRODUCTION'
)
SELECT (td - prev_td) AS td_delta_days,
       COUNT(*)        AS row_count
FROM   ordered
WHERE  prev_tno IS NOT NULL
AND    tno = prev_tno + 1
GROUP BY (td - prev_td)
ORDER BY td_delta_days;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
