/* ============================================================================
   23_efficiency_daily_summary.sql
   Project : Dost Textiles - Daily Loom Production Report
   Purpose : DOSTEX.WV_EFF_V confirmed real and live-calculated (see
             22_wv_eff_v_lookup.sql - view text builds EFF/EFF_100 from
             WV_PROD_V's RPM/PICK/TOT_SHIFTS/TOTAL_MTR/BWIDTH, nothing
             separately stored). Step 4 of that script returned 170 raw
             rows (per loom/beam/quality/shift) for Aug 1-7 2026 - too
             granular to hand-average accurately from wrapped SQL*Plus
             text. This aggregates precisely instead.

   Step 1  : Mill-wide daily summary - AVG/MIN/MAX(EFF), row count,
             SUM(TOTAL_MTR), SUM(EFF_100) per TD.
   Step 2  : Grand total/average across the whole Aug 1-7 week.
   Step 3  : Per-loom average efficiency for the week (matches "Loom Wise
             Efficiency" report's own grouping).
   Step 4  : Per-shift (A/B) average efficiency for the week (matches
             "Shift Wise Efficiency" report's own grouping).

   Target  : Oracle Database 11.2.0.1. Run as DOSTEX.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN td         FORMAT A11
COLUMN avg_eff    FORMAT 999.99
COLUMN min_eff    FORMAT 999.99
COLUMN max_eff    FORMAT 999.99
COLUMN row_count  FORMAT 9999
COLUMN total_mtr  FORMAT 999999
COLUMN eff_100    FORMAT 999999
COLUMN lcode      FORMAT 9999
COLUMN shift      FORMAT A6

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\efficiency_daily_summary_output.txt

PROMPT ============================================================
PROMPT STEP 1: Mill-wide daily efficiency summary, Aug 1-7 2026
PROMPT ============================================================

SELECT td,
       COUNT(*)         AS row_count,
       ROUND(AVG(eff),2) AS avg_eff,
       ROUND(MIN(eff),2) AS min_eff,
       ROUND(MAX(eff),2) AS max_eff,
       SUM(total_mtr)    AS total_mtr,
       SUM(eff_100)      AS eff_100
FROM   dostex.wv_eff_v
WHERE  td >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    td <= TO_DATE('07-AUG-2026','DD-MON-YYYY')
GROUP BY td
ORDER BY td;

PROMPT ============================================================
PROMPT STEP 2: Grand total/average across the whole Aug 1-7 week
PROMPT ============================================================

SELECT COUNT(*)          AS row_count,
       ROUND(AVG(eff),2)  AS avg_eff,
       ROUND(MIN(eff),2)  AS min_eff,
       ROUND(MAX(eff),2)  AS max_eff,
       SUM(total_mtr)     AS total_mtr,
       SUM(eff_100)       AS eff_100
FROM   dostex.wv_eff_v
WHERE  td >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    td <= TO_DATE('07-AUG-2026','DD-MON-YYYY');

PROMPT ============================================================
PROMPT STEP 3: Per-loom average efficiency, Aug 1-7 2026 (matches
PROMPT "Loom Wise Efficiency" report's own grouping)
PROMPT ============================================================

SELECT lcode,
       COUNT(*)          AS row_count,
       ROUND(AVG(eff),2)  AS avg_eff,
       SUM(total_mtr)     AS total_mtr
FROM   dostex.wv_eff_v
WHERE  td >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    td <= TO_DATE('07-AUG-2026','DD-MON-YYYY')
GROUP BY lcode
ORDER BY lcode;

PROMPT ============================================================
PROMPT STEP 4: Per-shift average efficiency, Aug 1-7 2026 (matches
PROMPT "Shift Wise Efficiency" report's own grouping)
PROMPT ============================================================

SELECT shift,
       COUNT(*)          AS row_count,
       ROUND(AVG(eff),2)  AS avg_eff,
       SUM(total_mtr)     AS total_mtr
FROM   dostex.wv_eff_v
WHERE  td >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
AND    td <= TO_DATE('07-AUG-2026','DD-MON-YYYY')
GROUP BY shift
ORDER BY shift;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
