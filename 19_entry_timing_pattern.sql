/* ============================================================================
   19_entry_timing_pattern.sql
   Project : Dost Textiles - Daily Loom Production Report
   Purpose : Empirically check the real data-entry TIME pattern, to base
             Task Scheduler's run time on actual evidence instead of the
             current guess (11:00 AM, picked with no supporting data - see
             README's Task Scheduler section).

   What's checked: DOSTEX.WPRDTM (the production MASTER table - one row
             per day for TT='PRODUCTION', confirmed via TNO incrementing
             ~1/day - see 11_confirm_active_schema.sql's Step 4 finding).
             FEEDD is the only candidate audit-timestamp column on this
             table - MOD D/MODUSERN/MODPCN/MODIPADD do NOT exist on
             DOSTEX.WPRDTM at all (confirmed via its full column list in
             10_production_tables_lookup.sql - those only existed on
             DOSTT.WPRDTD, which is the dead/retired schema). WPRDTD
             (DOSTEX) has no audit-timestamp columns whatsoever (confirmed
             via 18_wprdtd_rate_pick_check.sql's column list). So FEEDD on
             WPRDTM is the ONLY place this signal could possibly live.

   Known risk going in: an earlier broad check (11_confirm_active_schema.
             sql) found DOSTEX.WPRDTM's MAX(FEEDD) blank - but that wasn't
             filtered to TT='PRODUCTION' specifically, so this re-checks
             precisely rather than assuming that result carries over.
             Real possibility this comes back empty - if so, that's a
             valid, useful, actionable finding in itself (per the
             request: means the scheduled time needs a safety buffer and
             the "NOT ENTERED YET" retry/gap logic matters more than
             nailing the exact hour).

   Window  : Last 30 days (slightly wider than the requested 2-4 weeks,
             for more data points to spot a pattern or its absence).

   Steps   : 1 - FEEDD population + real-time-component check (is FEEDD
                 populated at all for PRODUCTION rows, and does it carry
                 an actual HH:MI:SS or is it just midnight/date-only?).
             2 - One row per production day: FEEDD's full timestamp,
                 entry lag in days, entry hour-of-day.
             3 - Distribution of entry hour-of-day across the window -
                 the actual "most days entered between X-Y" pattern.
             4 - Outlier check: days entered more than 1 day after TD.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN td            FORMAT A11
COLUMN feedd_ts        FORMAT A20
COLUMN entry_hour        FORMAT A5
COLUMN lag_days            FORMAT 999
COLUMN day_count              FORMAT 9999
COLUMN total_rows                FORMAT 9999
COLUMN has_feedd                    FORMAT 9999
COLUMN has_real_time                    FORMAT 9999

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\entry_timing_pattern_output.txt

PROMPT ============================================================
PROMPT STEP 1: FEEDD population + real-time-component check,
PROMPT TT='PRODUCTION', last 30 days. If HAS_REAL_TIME is 0, FEEDD
PROMPT carries no usable time-of-day signal at all (either NULL or
PROMPT always midnight).
PROMPT ============================================================

SELECT COUNT(*) AS total_rows,
       COUNT(feedd) AS has_feedd,
       COUNT(CASE WHEN feedd IS NOT NULL
                   AND TO_CHAR(feedd,'HH24MISS') != '000000'
                  THEN 1 END) AS has_real_time
FROM   dostex.wprdtm
WHERE  tt = 'PRODUCTION'
AND    td >= TRUNC(SYSDATE) - 30;

PROMPT ============================================================
PROMPT STEP 2: One row per production day - FEEDD's full timestamp,
PROMPT entry lag in days (FEEDD date minus TD), entry hour-of-day.
PROMPT ============================================================

SELECT td,
       TO_CHAR(feedd,'DD-MON-YYYY HH24:MI:SS') AS feedd_ts,
       (TRUNC(feedd) - td) AS lag_days,
       TO_CHAR(feedd,'HH24') AS entry_hour
FROM   dostex.wprdtm
WHERE  tt = 'PRODUCTION'
AND    td >= TRUNC(SYSDATE) - 30
ORDER BY td;

PROMPT ============================================================
PROMPT STEP 3: Distribution of entry hour-of-day across the window -
PROMPT the real pattern (or lack of one).
PROMPT ============================================================

SELECT TO_CHAR(feedd,'HH24') AS entry_hour,
       COUNT(*) AS day_count
FROM   dostex.wprdtm
WHERE  tt = 'PRODUCTION'
AND    td >= TRUNC(SYSDATE) - 30
AND    feedd IS NOT NULL
GROUP BY TO_CHAR(feedd,'HH24')
ORDER BY entry_hour;

PROMPT ============================================================
PROMPT STEP 4: Outlier check - days entered more than 1 day after
PROMPT their TD (later than the normal next-day pattern).
PROMPT ============================================================

SELECT td,
       TO_CHAR(feedd,'DD-MON-YYYY HH24:MI:SS') AS feedd_ts,
       (TRUNC(feedd) - td) AS lag_days
FROM   dostex.wprdtm
WHERE  tt = 'PRODUCTION'
AND    td >= TRUNC(SYSDATE) - 30
AND    feedd IS NOT NULL
AND    (TRUNC(feedd) - td) > 1
ORDER BY td;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
