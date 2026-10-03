/* ============================================================================
   17_contract_rate_diagnosis.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : 15_contract_rates.sql v3 ran cleanly (real column names,
             confirmed via Step 2 of the prior version) but returned
             PER_PICK_RATE/CONVER_PER_METER/GST_PER as NULL for all 7
             known contracts - including CONT_NO=69, the exact contract
             in Azam's worked example (0.76 x 66 = 50.16), which came
             back with the correct PICK=66 but a blank PER_PICK_RATE.
             So the real number the ERP actually uses isn't sitting in
             WC_CONT_SUB_V for these rows. Two live hypotheses:
               (a) it's on CONTM's HEADER-level columns instead (same
                   column names, but the sub-line just isn't filled in -
                   Step 1 checks this directly, bypassing the view)
               (b) it genuinely lives in WV_LOOM_PICKRATE (TRANS_ID,
                   PICK, RATE, CONV_RATE, LWIDTH) after all - dropped in
                   15 v3 as unnecessary once WC_CONT_SUB_V's columns
                   looked like a match, but "the ERP's own detail-grid
                   Rate field" (Azam's own words) sounds more like a
                   transaction-level grid than a contract-header form -
                   Steps 2-3 check this.

   Anchor  : CONT_NO=69 / quality 76X66 30/30 (PC-69/3343) / PICK=66 is
             the known-good example (expect PER_PICK_RATE=0.76,
             CONVER_PER_METER=50.16 somewhere) - use it to sanity-check
             whichever hypothesis pans out.

   Output  : Plain readable SQL*Plus tabular output (not CSV) - pure
             diagnosis, not a final export.
   ============================================================================ */

SET LINESIZE 250
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN cont_no        FORMAT A10
COLUMN quality          FORMAT A26
COLUMN per_pick_rate      FORMAT 990.99
COLUMN conver_per_meter    FORMAT 999990.99
COLUMN gst_per               FORMAT 990.99
COLUMN fabric_rate              FORMAT 999990.99
COLUMN yarn_rate                  FORMAT 999990.99
COLUMN cont_rate                    FORMAT 999990.99
COLUMN trans_no                        FORMAT 9999999
COLUMN pick                              FORMAT 9999
COLUMN rate                                FORMAT 999990.99
COLUMN conv_rate                             FORMAT 999990.99
COLUMN lwidth                                  FORMAT 9999

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\contract_rate_diagnosis_output.txt

PROMPT ============================================================
PROMPT STEP 1: CONTM HEADER-level values (bypassing WC_CONT_SUB_V
PROMPT entirely) for the same 7 CONT_NOs. If these are populated
PROMPT while the sub-line wasn't, that confirms hypothesis (a).
PROMPT ============================================================

SELECT c.cont_no,
       c.per_pick_rate,
       c.conver_per_meter,
       c.gst_per,
       c.fabric_rate,
       c.yarn_rate,
       c.cont_rate
FROM   dostex.contm c
WHERE  c.cont_no IN ('46','60','65','66','67','68','69')
ORDER BY c.cont_no;

PROMPT ============================================================
PROMPT STEP 2: Raw WV_LOOM_PICKRATE sample (unfiltered, most recent
PROMPT 30 by TRANS_ID) - real values, to check for anything in the
PROMPT 0.76 / 50.16 neighborhood (hypothesis b).
PROMPT ============================================================

SELECT * FROM (
    SELECT trans_id, pick, rate, conv_rate, lwidth
    FROM   dostex.wv_loom_pickrate
    ORDER BY trans_id DESC
) WHERE ROWNUM <= 30;

PROMPT ============================================================
PROMPT STEP 3: WV_LOOM_PICKRATE joined to our known CONT_NO=69 /
PROMPT PC-69/3343 production transactions specifically (TRANS_ID =
PROMPT TRANS_NO, the same join-key guess as before) - if this
PROMPT returns rows with PICK=66 and RATE around 0.76, hypothesis
PROMPT (b) is confirmed for real. Zero rows = join key guess wrong
PROMPT or hypothesis (b) is wrong too - not an error either way.
PROMPT ============================================================

SELECT DISTINCT
       v.cont_no,
       v.quality,
       v.trans_no,
       wp.pick,
       wp.rate,
       wp.conv_rate,
       wp.lwidth
FROM   dostex.wv_prod_v v
JOIN   dostex.wv_loom_pickrate wp ON wp.trans_id = v.trans_no
WHERE  v.cont_no = '69'
ORDER BY v.trans_no;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
