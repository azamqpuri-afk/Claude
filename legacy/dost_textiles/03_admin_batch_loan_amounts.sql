/* ============================================================================
   03_admin_batch_loan_amounts.sql
   Project : Dost Textiles - ERP Backdate Detection
   Purpose : Follow-up on 02_emp_loans_backdate.sql's headline finding -
             virtually all of PAYROLL.EMP_LOANS' flagged (backdated /
             future-dated) rows were entered by ADMIN from IP
             192.168.18.199 / PC "1-PC", in distinct batches rather than
             scattered individually. This groups those batches by the
             actual entry date (FEEDD) and sums LOAN_AMOUNT per batch, so
             the dollar (rupee) size of each batch is visible - a batch
             that's 40 small loans is a different story than a batch of
             5 large ones.

   Scope   : PAYROLL.EMP_LOANS only (that's where this pattern was found).
             Includes ALL of ADMIN's entries for that IP, not just the
             flagged ones, so you can see whether ADMIN ever enters loans
             on-time too, or if every single batch is backdated.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN entry_date       FORMAT A11
COLUMN loan_count       FORMAT 9999
COLUMN distinct_emps    FORMAT 9999
COLUMN total_loan_amt   FORMAT 999,999,999
COLUMN total_installmt  FORMAT 999,999,999
COLUMN loan_date_min    FORMAT A11
COLUMN loan_date_max    FORMAT A11
COLUMN avg_gap_days     FORMAT 9999.9
COLUMN max_gap_days     FORMAT 9999
COLUMN backdated_cnt    FORMAT 9999
COLUMN future_cnt       FORMAT 9999
COLUMN normal_cnt       FORMAT 9999

PROMPT ============================================================
PROMPT Every batch of loans ADMIN (192.168.18.199 / 1-PC) entered
PROMPT into PAYROLL.EMP_LOANS, by entry date (FEEDD), with the total
PROMPT rupee amount per batch and how many rows in it were flagged.
PROMPT ============================================================

WITH admin_rows AS (
    SELECT emp_code,
           loan_amount,
           installment_amount,
           TRUNC(loan_date) AS loan_date,
           TRUNC(feedd)     AS entry_date,
           (TRUNC(feedd) - TRUNC(loan_date)) AS gap_days
    FROM   payroll.emp_loans
    WHERE  usern = 'ADMIN'
    AND    ipadd = '192.168.18.199'
)
SELECT entry_date,
       COUNT(*)                            AS loan_count,
       COUNT(DISTINCT emp_code)             AS distinct_emps,
       SUM(loan_amount)                     AS total_loan_amt,
       SUM(installment_amount)              AS total_installmt,
       MIN(loan_date)                       AS loan_date_min,
       MAX(loan_date)                       AS loan_date_max,
       ROUND(AVG(gap_days), 1)              AS avg_gap_days,
       MAX(gap_days)                        AS max_gap_days,
       COUNT(CASE WHEN gap_days > 7  THEN 1 END) AS backdated_cnt,
       COUNT(CASE WHEN gap_days < 0  THEN 1 END) AS future_cnt,
       COUNT(CASE WHEN gap_days BETWEEN 0 AND 7 THEN 1 END) AS normal_cnt
FROM   admin_rows
GROUP BY entry_date
ORDER BY entry_date;

PROMPT ============================================================
PROMPT Grand total across all ADMIN batches (sanity check against
PROMPT the per-batch rows above, and a single headline number).
PROMPT ============================================================

SELECT COUNT(*)                AS total_admin_rows,
       COUNT(DISTINCT emp_code) AS total_distinct_emps,
       SUM(loan_amount)         AS grand_total_loan_amt,
       COUNT(CASE WHEN (TRUNC(feedd) - TRUNC(loan_date)) > 7 THEN 1 END) AS total_backdated,
       COUNT(CASE WHEN (TRUNC(feedd) - TRUNC(loan_date)) < 0 THEN 1 END) AS total_future_dated
FROM   payroll.emp_loans
WHERE  usern = 'ADMIN'
AND    ipadd = '192.168.18.199';
