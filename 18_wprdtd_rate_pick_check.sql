/* ============================================================================
   18_wprdtd_rate_pick_check.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : 16_loom_quality_history_with_contract.sql v2's ACTUAL_PICK
             (WPRDTD.PICK, joined via TRANS_NO/SR_NO) came back NULL on
             all 145 rows in the Aug 1-6 2026 window - not a join bug
             (full row count returned), a genuine data-population gap.

             Before concluding Net Sale can't be computed per-row, check
             a field I should have checked first: Azam described the
             confirmed number (50.16 for CONT_NO=69) as matching "the
             ERP's own detail-grid Rate field" - WPRDTD (the production
             DETAIL table) has its OWN separate RATE column (position 2,
             right after QCODE - a core field) that's completely distinct
             from CONTM.PER_PICK_RATE and from WPRDTD.PICK. "Detail grid"
             is a strong textual hint this is what Azam actually means -
             if WPRDTD.RATE is populated and matches 50.16 for CONT_NO=69
             directly, that's a much more direct Net Sale source than
             ACTUAL_PICK x PER_PICK_RATE (no multiplication/join to CONTM
             needed at all - just read the value straight off the
             production row).

   Step 1  : Table-wide population check for WPRDTD.PICK vs WPRDTD.RATE
             (COUNT(*) vs COUNT(each column)) - settles whether PICK is
             essentially a dead/unused column overall, or just empty for
             this window, and whether RATE is reliably populated by
             comparison.
   Step 2  : Raw WPRDTD rows (RATE, PICK, QCODE, UC, and identifying
             info) for CONT_NO=69's actual production transactions
             specifically - the known-good anchor (expect RATE near
             50.16 if this hypothesis is right).

   Output  : Plain readable SQL*Plus tabular output (not CSV) - pure
             diagnosis, not a final export.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN trans_no  FORMAT 9999999
COLUMN sr_no       FORMAT 9999
COLUMN rate           FORMAT 999990.99
COLUMN pick              FORMAT 9999
COLUMN qcode                FORMAT 9999
COLUMN cont_no                 FORMAT A10

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\wprdtd_rate_pick_check_output.txt

PROMPT ============================================================
PROMPT STEP 1: Table-wide population check - WPRDTD.PICK vs
PROMPT WPRDTD.RATE, whole table (not just our date window).
PROMPT ============================================================

SELECT COUNT(*)       AS total_rows,
       COUNT(pick)     AS has_pick,
       COUNT(rate)      AS has_rate
FROM   dostex.wprdtd;

PROMPT ============================================================
PROMPT STEP 2: Raw WPRDTD rows for CONT_NO=69's actual production
PROMPT transactions (Aug 1-6 2026 window) - RATE, PICK, and
PROMPT identifying info. Known-good anchor: expect RATE near 50.16
PROMPT if WPRDTD.RATE is the real per-row conversion rate.
PROMPT ============================================================

SELECT wd.trans_no,
       wd.sr_no,
       wd.rate,
       wd.pick,
       wd.qcode,
       v.cont_no
FROM   dostex.wv_prod_v v
JOIN   dostex.wprdtd wd ON wd.trans_no = v.trans_no AND wd.sr_no = v.sr_no
WHERE  v.cont_no = '69'
AND    TRUNC(v.td) >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    TRUNC(v.td) <= TRUNC(SYSDATE)
ORDER BY wd.trans_no, wd.sr_no;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
