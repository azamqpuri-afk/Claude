/* ============================================================================
   12_daily_production_report.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : The actual report query. Confirmed via 11_confirm_active_schema:
             DOSTEX is the live schema (WPRDTM.TD up to 06-AUG-26, one new
             PRODUCTION transaction per day); DOSTT stopped Oct-2023 and is
             not needed here. Built directly on DOSTEX.WV_PROD_V, which
             already encodes the correct join (WPRDTM+WPRDTD+WYBEAMS+CONTM+
             WC_CONT_SUB_V+BEAMS+QUALITIES+WV_LOOMS_V, filtered to
             TT='PRODUCTION') - see scripts/10_production_tables_lookup.sql
             for that view's full text.

   Report date : v3 correction (2026-08-07, from the business, not a bug):
             production data for a given day is entered into the ERP the
             NEXT day (like a loom scoreboard) - so 07-AUG-26's "no rows
             selected" wasn't a bug, it was correctly finding that today's
             (07-AUG) data doesn't exist yet, because it isn't due to be
             entered until tomorrow. The report's target date is therefore
             ALWAYS "yesterday" (TRUNC(SYSDATE) - 1), not "today" - this is
             the real production filter now (see 13_daily_production_email.py
             for the check-before-send / gap-check logic this feeds into).
             A hardcoded historical-date fallback is left commented out
             below each active line for offline debugging (e.g. running
             this outside the data-entry cadence, or after the live window
             has moved past 06-AUG-2026). No DEFINE/substitution variables
             used here, matching the rest of this project's scripts
             (DEFINE caused real bugs earlier - see 01's header).

   v2 fix (2026-08-07): Step 2's LISTAGG(DISTINCT ...) errored with
             ORA-30482 - LISTAGG's DISTINCT keyword needs Oracle 12c+
             (SYNTAX addition, not just a version quirk), this instance is
             11.2.0.1. Fixed by pre-deduplicating quality/shift per loom
             in a subquery (plain SELECT DISTINCT, which IS 11.2-safe),
             then LISTAGG-ing the already-distinct rows - same output,
             11.2-compatible.

   Loom range : Currently ALL looms (no LCODE filter) - the source screen
             takes a From/To loom range, but a daily email report probably
             wants the whole floor by default. Add
             "AND v.lcode BETWEEN <from> AND <to>" to Step 2's WHERE clause
             if a subset is ever needed.

   Output  : Step 1 - detail rows: one per loom/quality/shift/beam
             transaction line for the report date, with loom name (via
             LOOMS), quality, shift, meters, faults, party, shed/section.
           Step 2 - per-loom daily summary: total meters, total faults,
             transaction line count, and which shifts/qualities ran - the
             shape most useful for an at-a-glance email body.
           Step 3 - single grand-total row (mill-wide, target date) - the
             headline number for the email subject line / top of body.
             NOTE: Step 3 returning 0 rows/NULL totals is a real, expected
             case (yesterday's data not entered yet) - 13_daily_production_
             email.py must check for this and send a "NOT ENTERED YET"
             notice instead of treating it as a query failure. See that
             script's header for the full not-yet-entered / gap-check spec.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN loom_name     FORMAT A12
COLUMN quality        FORMAT A20
COLUMN shift           FORMAT A6
COLUMN total_mtr        FORMAT 9,999,999.99
COLUMN total_faults      FORMAT 9999
COLUMN party               FORMAT A20
COLUMN shed_no               FORMAT A6
COLUMN prod_date               FORMAT A11

PROMPT ============================================================
PROMPT STEP 1: Detail rows - every production transaction line for
PROMPT the report date, loom-name-labeled.
PROMPT ============================================================

SELECT v.td                    AS prod_date,
       lm.lname                AS loom_name,
       v.shed_no,
       v.section_no,
       v.quality,
       v.shift,
       v.tot_shifts,
       v.total_mtr,
       v.total_faults,
       v.rpm,
       v.pick,
       v.party,
       v.cont_no,
       v.beam_code,
       v.set_no,
       v.rem
FROM   dostex.wv_prod_v v
JOIN   dostex.looms lm ON lm.lcode = v.lcode
-- production: yesterday (entry lag)
WHERE  TRUNC(v.td) = TRUNC(SYSDATE) - 1
-- offline debug fallback:
-- WHERE  TRUNC(v.td) = TO_DATE('06-AUG-2026','DD-MON-YYYY')
ORDER BY lm.lcode, v.shift;

PROMPT ============================================================
PROMPT STEP 2: Per-loom daily summary - the shape an email body
PROMPT actually wants (one line per loom, not per transaction).
PROMPT ============================================================

WITH day_rows AS (
    SELECT v.lcode, v.quality, v.shift, v.total_mtr, v.total_faults
    FROM   dostex.wv_prod_v v
    -- production: yesterday (entry lag)
    WHERE  TRUNC(v.td) = TRUNC(SYSDATE) - 1
    -- offline debug fallback:
    -- WHERE  TRUNC(v.td) = TO_DATE('06-AUG-2026','DD-MON-YYYY')
),
dedup_quality AS (
    SELECT DISTINCT lcode, quality FROM day_rows
),
dedup_shift AS (
    SELECT DISTINCT lcode, shift FROM day_rows
),
qualities_agg AS (
    SELECT lcode, LISTAGG(quality, ', ') WITHIN GROUP (ORDER BY quality) AS qualities
    FROM   dedup_quality
    GROUP BY lcode
),
shifts_agg AS (
    SELECT lcode, LISTAGG(shift, ', ') WITHIN GROUP (ORDER BY shift) AS shifts_ran
    FROM   dedup_shift
    GROUP BY lcode
)
SELECT lm.lcode                          AS loom_no,
       lm.lname                          AS loom_name,
       lm.section_no,
       lm.sec_jobber,
       COUNT(*)                          AS line_count,
       SUM(dr.total_mtr)                 AS total_mtr,
       SUM(dr.total_faults)              AS total_faults,
       qa.qualities,
       sa.shifts_ran
FROM   day_rows dr
JOIN   dostex.looms lm    ON lm.lcode = dr.lcode
LEFT JOIN qualities_agg qa ON qa.lcode = dr.lcode
LEFT JOIN shifts_agg sa    ON sa.lcode = dr.lcode
GROUP BY lm.lcode, lm.lname, lm.section_no, lm.sec_jobber, qa.qualities, sa.shifts_ran
ORDER BY lm.lcode;

PROMPT ============================================================
PROMPT STEP 3: Mill-wide grand total for the report date - the
PROMPT single headline number (email subject / top of body).
PROMPT ============================================================

-- production: yesterday (entry lag)
SELECT TRUNC(SYSDATE) - 1                AS prod_date,
-- offline debug fallback:
-- SELECT TO_DATE('06-AUG-2026','DD-MON-YYYY') AS prod_date,
       COUNT(DISTINCT v.lcode)           AS looms_reporting,
       COUNT(*)                          AS total_lines,
       SUM(v.total_mtr)                  AS total_mtr,
       SUM(v.total_faults)               AS total_faults
FROM   dostex.wv_prod_v v
-- production: yesterday (entry lag)
WHERE  TRUNC(v.td) = TRUNC(SYSDATE) - 1
-- offline debug fallback:
-- WHERE  TRUNC(v.td) = TO_DATE('06-AUG-2026','DD-MON-YYYY')
;
