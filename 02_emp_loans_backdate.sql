/* ============================================================================
   02_emp_loans_backdate.sql  (v2 - 2026-08-07)
   Project : Dost Textiles - ERP Backdate Detection
   Purpose : Backdate-detection query against PAYROLL.EMP_LOANS and
             DOST_PAYROLL.EMP_LOANS (employee loan disbursements).

   v2 changelog (fixes a real bug found in v1's output):
     - PAYROLL.EMP_LOANS.FEEDING_DATE is 100% NULL (confirmed via Step C:
       HAS_FEEDING_DATE = 0 of 1090 rows) - v1 used FEEDING_DATE alone as
       the audit anchor, so EVERY PAYROLL row came back "MISSING DATE",
       silently failing to assess that entire schema. Fixed by using
       COALESCE(FEEDING_DATE, FEEDD) - DOST_PAYROLL has no FEEDD column
       (aliased to NULL in its half of the UNION), so it always falls
       through to FEEDING_DATE there; PAYROLL falls through to FEEDD.
     - Added AUDIT_SOURCE column so each row shows which column actually
       supplied the audit date (transparency - COALESCE hides that).
     - Added attribution columns found via Step A: EMP_CODE, LOAN_AMOUNT,
       INSTALLMENT_AMOUNT, and who/where it was entered (ENTERED_BY /
       ENTRY_IP / ENTRY_PC - column names differ per schema, aliased to a
       common name here: DOST_PAYROLL uses USER_NAME/IP_ADD/PC_NAME,
       PAYROLL uses USERN/IPADD/PCN).

   Logic   : GAP_DAYS = AUDIT_DATE - LOAN_DATE
               large positive -> loan date predates the entry: backdating
               negative       -> loan date is AFTER the entry date (see
                                 v1 finding: DOST_PAYROLL's negative-gap
                                 rows cluster at month-end loan dates -
                                 looks like a period-end booking convention,
                                 not necessarily fraud; still flagged for
                                 you to judge, just don't assume it's the
                                 same risk category as BACKDATED).

   Caveat  : FEEDD/FEEDING_DATE are the best available proxy for "system
             timestamp" in this ERP (no Forms source to confirm they're
             truly non-editable in the UI). Treat flagged rows as leads,
             not proof - especially now that PAYROLL is being assessed via
             FEEDD for the first time; worth sanity-checking a few PAYROLL
             results against Step A's MODD/MODUSERN too.

   Tuning  : BACKDATE_THRESHOLD_DAYS (currently 7) appears once, in the
             `flagged` CTE.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

PROMPT ============================================================
PROMPT STEP C (moved up): confirm FEEDD actually covers the rows
PROMPT where FEEDING_DATE is NULL, before relying on it as fallback.
PROMPT ============================================================

SELECT 'PAYROLL' AS source_schema, COUNT(*) AS total_rows,
       COUNT(loan_date) AS has_loan_date,
       COUNT(feeding_date) AS has_feeding_date,
       COUNT(feedd) AS has_feedd,
       COUNT(loan_amount) AS has_loan_amount
FROM   payroll.emp_loans
UNION ALL
SELECT 'DOST_PAYROLL', COUNT(*),
       COUNT(loan_date), COUNT(feeding_date),
       0 AS has_feedd,   -- DOST_PAYROLL.EMP_LOANS has no FEEDD column
       COUNT(loan_amount)
FROM   dost_payroll.emp_loans;

PROMPT ============================================================
PROMPT STEP D: Backdate flags on EMP_LOANS (PAYROLL + DOST_PAYROLL)
PROMPT AUDIT_DATE = COALESCE(FEEDING_DATE, FEEDD). Worst gaps first.
PROMPT ============================================================

COLUMN source_schema  FORMAT A14
COLUMN emp_code       FORMAT 999999
COLUMN loan_amount    FORMAT 999,999,999
COLUMN entered_by     FORMAT A16
COLUMN entry_ip       FORMAT A16
COLUMN loan_date      FORMAT A11
COLUMN audit_date     FORMAT A11
COLUMN audit_source   FORMAT A12
COLUMN gap_days       FORMAT 99999
COLUMN flag           FORMAT A45

WITH loans AS (
    SELECT 'PAYROLL' AS source_schema, ROWID AS row_id,
           emp_code, loan_amount, installment_amount,
           usern AS entered_by, ipadd AS entry_ip, pcn AS entry_pc,
           TRUNC(loan_date) AS loan_date,
           TRUNC(voucher_date) AS voucher_date,
           TRUNC(slip_date) AS slip_date,
           TRUNC(feeding_date) AS feeding_date,
           TRUNC(feedd) AS feedd
    FROM   payroll.emp_loans
    UNION ALL
    SELECT 'DOST_PAYROLL' AS source_schema, ROWID AS row_id,
           emp_code, loan_amount, installment_amount,
           user_name AS entered_by, ip_add AS entry_ip, pc_name AS entry_pc,
           TRUNC(loan_date) AS loan_date,
           TRUNC(voucher_date) AS voucher_date,
           TRUNC(slip_date) AS slip_date,
           TRUNC(feeding_date) AS feeding_date,
           TO_DATE(NULL) AS feedd   -- DOST_PAYROLL.EMP_LOANS has no FEEDD column
    FROM   dost_payroll.emp_loans
),
flagged AS (
    SELECT source_schema, row_id, emp_code, loan_amount, installment_amount,
           entered_by, entry_ip, entry_pc,
           loan_date, voucher_date, slip_date,
           COALESCE(feeding_date, feedd) AS audit_date,
           CASE WHEN feeding_date IS NOT NULL THEN 'FEEDING_DATE'
                WHEN feedd IS NOT NULL THEN 'FEEDD'
                ELSE NULL END AS audit_source,
           (COALESCE(feeding_date, feedd) - loan_date) AS gap_days,
           CASE
             WHEN loan_date IS NULL OR COALESCE(feeding_date, feedd) IS NULL
                  THEN 'MISSING DATE - cannot assess'
             WHEN (COALESCE(feeding_date, feedd) - loan_date) > 7   -- <-- BACKDATE_THRESHOLD_DAYS, edit here
                  THEN 'BACKDATED - entered ' || (COALESCE(feeding_date, feedd) - loan_date) || ' days after loan date'
             WHEN (COALESCE(feeding_date, feedd) - loan_date) < 0
                  THEN 'FUTURE-DATED - loan date is AFTER entry date'
             ELSE 'NORMAL (<= 7 day lag)'
           END AS flag
    FROM   loans
)
SELECT *
FROM   flagged
WHERE  flag != 'NORMAL (<= 7 day lag)'
ORDER BY gap_days DESC NULLS LAST;

PROMPT ============================================================
PROMPT STEP E: Flag-count summary by schema, so the detail above can
PROMPT be read as a proportion instead of a wall of rows.
PROMPT ============================================================

WITH loans AS (
    SELECT 'PAYROLL' AS source_schema,
           TRUNC(loan_date) AS loan_date,
           COALESCE(TRUNC(feeding_date), TRUNC(feedd)) AS audit_date
    FROM   payroll.emp_loans
    UNION ALL
    SELECT 'DOST_PAYROLL',
           TRUNC(loan_date),
           TRUNC(feeding_date)
    FROM   dost_payroll.emp_loans
)
SELECT source_schema,
       COUNT(*) AS total_rows,
       COUNT(CASE WHEN loan_date IS NULL OR audit_date IS NULL THEN 1 END) AS missing_date,
       COUNT(CASE WHEN audit_date - loan_date > 7 THEN 1 END) AS backdated,
       COUNT(CASE WHEN audit_date - loan_date < 0 THEN 1 END) AS future_dated,
       COUNT(CASE WHEN audit_date - loan_date BETWEEN 0 AND 7 THEN 1 END) AS normal
FROM   loans
GROUP BY source_schema
ORDER BY source_schema;
