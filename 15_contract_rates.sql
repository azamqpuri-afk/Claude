/* ============================================================================
   15_contract_rates.sql  (v4 - 2026-08-08)
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : Contract-linked rate data for the loom production export.

   v4 changelog (from 17_contract_rate_diagnosis.sql's real findings):
       - PER_PICK_RATE/CONVER_PER_METER/GST_PER/FABRIC_RATE/YARN_RATE/
         CONT_RATE live on CONTM's HEADER (alias c), NOT on
         WC_CONT_SUB_V's per-quality sub-line (alias sv) as v3 assumed -
         v3's pull from sv came back entirely NULL for these columns even
         though the column names exist there too. Switched to c.* for all
         of these.
       - WV_LOOM_PICKRATE (the v2 detour) is CONFIRMED DEAD, not just a
         wrong join key - Step 2 of the diagnosis script queried it with
         zero filters and got "no rows selected": the table is completely
         empty. Not referenced anywhere in this script anymore.
       - CONVER_PER_METER IS ALREADY GST-INCLUSIVE - confirmed by real
         data: CONT_NO=69 has PER_PICK_RATE=0.76, PICK=66, GST_PER=18;
         0.76*66=50.16, *1.18=59.19, which matches the STORED
         CONVER_PER_METER (59.19) exactly. v3's CONVERSION_WITH_GST
         column would have applied GST a second time on top of an
         already-GST-inclusive number - WRONG, removed. Replaced with
         CALC_PRE_GST_CHECK (=PER_PICK_RATE*PICK, independently computed)
         next to CONVER_PER_METER_PRE_GST (=stored CONVER_PER_METER with
         GST backed out) - these two should match if the formula holds;
         a mismatch is a real, visible flag (see CONT_NO=66 below).
       - REAL DATA CAVEAT, not a query bug: only 3 of the 7 known
         contracts (65, 66, 69) have ANY rate data populated anywhere
         (CONTM header - confirmed the only real source). The other 4
         (46, 60, 67, 68) are genuinely NULL/unset for PER_PICK_RATE,
         CONVER_PER_METER, and GST_PER - not found on WC_CONT_SUB_V,
         CONTM, or WV_LOOM_PICKRATE. This script will correctly show
         blanks for those 4 - that's accurate, not something to chase
         further via more schema guessing. Worth a direct question to
         Azam: are those 4 priced some other way outside this ERP, or is
         this a real data-entry gap?
       - CONT_NO=66 doesn't fit the formula even with real data present
         (PER_PICK_RATE=0.76, PICK=76, GST_PER=0 -> expect 57.76, stored
         CONVER_PER_METER=53.20) - flagged via the two cross-check
         columns below, not silently smoothed over. Likely a real-world
         contract-data quirk (renegotiated rate, rounding, etc.), not a
         SQL issue - not investigated further here.

   PICK still comes from WC_CONT_SUB_V (alias sv) - confirmed correctly
   populated there in both v1 and v3 runs, and it's the same source
   WV_PROD_V itself uses via CONT_SUB_ID. WIDTH still comes from CONTM
   (header-level only, doesn't vary per quality).

   Item 1 (which table CONT_NO links to) - confirmed via WV_PROD_V's view
   text (10_production_tables_lookup.sql) AND via Step 1's formal FK
   check below: CONTM (contract header), joined WPRDTD.CONT_ID =
   CONTM.CONT_ID(+) - a LEFT join, so non-contract production rows have
   CONT_NO = NULL.

   Scope   : Only CONT_NO/quality combinations that actually appear in
             our Aug 1-6 2026 production window (same range as scripts
             13/14/16), pulled from WV_PROD_V (already filters to
             TT='PRODUCTION').

   Output  : Plain readable SQL*Plus tabular output (not CSV) - matches
             this project's convention for lookup/exploration scripts.
   ============================================================================ */

SET LINESIZE 250
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN cont_no          FORMAT A10
COLUMN cont_date         FORMAT A11
COLUMN cont_type          FORMAT A12
COLUMN party_code          FORMAT A10
COLUMN quality               FORMAT A26
COLUMN pick                     FORMAT 9999
COLUMN per_pick_rate              FORMAT 990.99
COLUMN conver_per_meter            FORMAT 999990.99
COLUMN calc_pre_gst_check           FORMAT 999990.99
COLUMN gst_per                        FORMAT 990.99
COLUMN conver_per_meter_pre_gst        FORMAT 999990.99
COLUMN width                              FORMAT 9999
COLUMN expiry_date                        FORMAT A11
COLUMN notional_fabric_rate                FORMAT 999990.99

SPOOL C:\Users\DBSERVER\DostTextiles\scripts\contract_rates_output.txt

PROMPT ============================================================
PROMPT STEP 1: Formal FK check - WPRDTD's real DB-enforced foreign
PROMPT keys (confirms CONTM link independently of the view text).
PROMPT ============================================================

SELECT a.table_name AS child_table,
       a.constraint_name,
       acc.column_name AS child_column,
       r.table_name AS parent_table,
       rcc.column_name AS parent_column
FROM   all_constraints a
JOIN   all_cons_columns acc
       ON acc.owner = a.owner AND acc.constraint_name = a.constraint_name
JOIN   all_constraints r
       ON r.owner = a.r_owner AND r.constraint_name = a.r_constraint_name
JOIN   all_cons_columns rcc
       ON rcc.owner = r.owner AND rcc.constraint_name = r.constraint_name
       AND rcc.position = acc.position
WHERE  a.constraint_type = 'R'
AND    a.table_name = 'WPRDTD'
ORDER BY a.constraint_name, acc.position;

PROMPT ============================================================
PROMPT STEP 2: THE ACTUAL DELIVERABLE - Per Pick Rate, Picks,
PROMPT Conversion Per Mtr (stored directly - already GST-inclusive,
PROMPT confirmed via real data, see header), Width, and Expiry Date,
PROMPT per CONT_NO/QUALITY, for the known qualities in the Aug 1-6
PROMPT 2026 production window. Rate fields (PER_PICK_RATE,
PROMPT CONVER_PER_METER, GST_PER, FABRIC_RATE) now pulled from CONTM
PROMPT (header) per 17_contract_rate_diagnosis.sql's finding - WILL BE
PROMPT BLANK for CONT_NO 46/60/67/68 (confirmed genuinely unset, not
PROMPT a query bug - see header). FABRIC_RATE included for reference
PROMPT only - NOT revenue (Azam's clarification).
PROMPT ============================================================

SELECT DISTINCT
       sv.cont_no,
       c.cont_date,
       c.cont_type,
       c.party_code,
       sv.quality,
       sv.pick,
       c.per_pick_rate,
       c.conver_per_meter,
       ROUND(c.per_pick_rate * sv.pick, 2)                          AS calc_pre_gst_check,
       c.gst_per,
       ROUND(c.conver_per_meter / (1 + NVL(c.gst_per,0)/100), 2)    AS conver_per_meter_pre_gst,
       c.width,
       c.expiry_date,
       c.fabric_rate                                                AS notional_fabric_rate
FROM   dostex.wc_cont_sub_v sv
JOIN   dostex.contm c ON c.cont_id = sv.cont_id
WHERE  sv.cont_sub_id IN (
    SELECT DISTINCT v.cont_sub_id
    FROM   dostex.wv_prod_v v
    WHERE  TRUNC(v.td) >= TO_DATE('01-AUG-2026','DD-MON-YYYY')
    AND    TRUNC(v.td) <= TRUNC(SYSDATE)
    AND    v.cont_sub_id IS NOT NULL
)
ORDER BY sv.cont_no, sv.quality;

SPOOL OFF

SET PAGESIZE 100
SET FEEDBACK ON
