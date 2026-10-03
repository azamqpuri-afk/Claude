/* ============================================================================
   01_schema_exploration.sql
   Project : Dost Textiles - ERP Backdate Detection
   Purpose : Discover production / yarn / financial tables (and their date
             columns) as candidates for backdate-detection analysis, while
             excluding Oracle system schemas.

   Target  : Oracle Database 11.2.0.1 (confirmed). Tested compatible with
             11g - no 12c+ (DBA_USERS.ORACLE_MAINTAINED) or 23ai+ (LIKE ANY)
             features used.

   Access  : If you connect with one of the app schema logins (DOSTEX,
             DOSTT, DOSTT_GST, DOST_PAYROLL, PAYROLL, PURI_PAYROLL) rather
             than a DBA/SYSTEM account, ALL_TABLES / ALL_TAB_COLUMNS will
             only return objects THAT login owns or has been granted -
             not the whole instance. Run this once per login to cover every
             module, or connect as SYSTEM/SYS if you have that password too.

   Usage   : Run in SQL*Plus / PL-SQL Developer as a SCRIPT (F8 in PL/SQL
             Developer, or "@01_schema_exploration.sql" in SQL*Plus).
             The system-schema exclusion list is hardcoded literally into
             each query below (NOT a DEFINE substitution variable) - older
             SQL*Plus clients cap DEFINE values at 240 characters, which
             this list exceeds. Edit the literal IN-list in each query if
             you need to change it; a text find/replace across the file
             covers all occurrences.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON
COLUMN owner            FORMAT A20
COLUMN table_name       FORMAT A32
COLUMN column_name      FORMAT A32
COLUMN data_type        FORMAT A18
COLUMN num_rows         FORMAT 999,999,999

/* ----------------------------------------------------------------------
   1. Keyword list identifying production / yarn / financial modules.
      Edit freely to match Dost Textiles' actual naming convention once
      you see the real table names.
   ---------------------------------------------------------------------- */

PROMPT ============================================================
PROMPT Connected as:
PROMPT ============================================================
SELECT USER AS connected_as FROM dual;

PROMPT ============================================================
PROMPT STEP 1: Candidate tables (name matches a module keyword)
PROMPT visible to this login, excluding Oracle system schemas
PROMPT ============================================================

SELECT t.owner,
       t.table_name,
       t.num_rows,
       t.last_analyzed
FROM   all_tables t
WHERE  t.owner NOT IN ('SYS','SYSTEM','SYSMAN','MGMT_VIEW','OUTLN','DBSNMP','APPQOSSYS','XDB','CTXSYS','MDSYS','OLAPSYS','ORDDATA','ORDSYS','ORDPLUGINS','WMSYS','EXFSYS','ANONYMOUS','XS$NULL','SPATIAL_CSW_ADMIN_USR','SPATIAL_WFS_ADMIN_USR','MDDATA','OWBSYS','OWBSYS_AUDIT','SI_INFORMTN_SCHEMA','ORACLE_OCM','APEX_PUBLIC_USER','APEX_030200','FLOWS_FILES','FLOWS_030000','DIP','TSMSYS','SCOTT','HR','OE','PM','IX','SH','BI')
AND    t.table_name NOT LIKE 'SYS\_%' ESCAPE '\'
AND  (
       UPPER(t.table_name) LIKE '%PRODUCTION%' OR UPPER(t.table_name) LIKE '%PROD%'
    OR UPPER(t.table_name) LIKE '%YARN%'       OR UPPER(t.table_name) LIKE '%SPIN%'
    OR UPPER(t.table_name) LIKE '%WEAV%'       OR UPPER(t.table_name) LIKE '%DYE%'
    OR UPPER(t.table_name) LIKE '%KNIT%'       OR UPPER(t.table_name) LIKE '%FINISH%'
    OR UPPER(t.table_name) LIKE '%FABRIC%'     OR UPPER(t.table_name) LIKE '%GREY%'
    OR UPPER(t.table_name) LIKE '%GRAY%'       OR UPPER(t.table_name) LIKE '%COUNT%'
    OR UPPER(t.table_name) LIKE '%LOT%'        OR UPPER(t.table_name) LIKE '%BATCH%'
    OR UPPER(t.table_name) LIKE '%SHIFT%'      OR UPPER(t.table_name) LIKE '%MACHINE%'
    OR UPPER(t.table_name) LIKE '%LOOM%'       OR UPPER(t.table_name) LIKE '%DISPATCH%'
    OR UPPER(t.table_name) LIKE '%ISSUE%'      OR UPPER(t.table_name) LIKE '%CONSUMPTION%'
    OR UPPER(t.table_name) LIKE '%WASTAGE%'    OR UPPER(t.table_name) LIKE '%INVENTORY%'
    OR UPPER(t.table_name) LIKE '%STOCK%'      OR UPPER(t.table_name) LIKE '%STORE%'
    OR UPPER(t.table_name) LIKE '%PURCHASE%'   OR UPPER(t.table_name) LIKE '%PO\_%' ESCAPE '\'
    OR UPPER(t.table_name) LIKE '%GRN%'        OR UPPER(t.table_name) LIKE '%SALES%'
    OR UPPER(t.table_name) LIKE '%INVOICE%'    OR UPPER(t.table_name) LIKE '%VOUCHER%'
    OR UPPER(t.table_name) LIKE '%PAYMENT%'    OR UPPER(t.table_name) LIKE '%RECEIPT%'
    OR UPPER(t.table_name) LIKE '%LEDGER%'     OR UPPER(t.table_name) LIKE '%JOURNAL%'
    OR UPPER(t.table_name) LIKE '%GL\_%' ESCAPE '\'   OR UPPER(t.table_name) LIKE '%AP\_%' ESCAPE '\'
    OR UPPER(t.table_name) LIKE '%AR\_%' ESCAPE '\'   OR UPPER(t.table_name) LIKE '%COST%'
    OR UPPER(t.table_name) LIKE '%BUDGET%'     OR UPPER(t.table_name) LIKE '%ACCOUNT%'
    OR UPPER(t.table_name) LIKE '%BANK%'       OR UPPER(t.table_name) LIKE '%CHEQUE%'
    OR UPPER(t.table_name) LIKE '%CASH%'       OR UPPER(t.table_name) LIKE '%TAX%'
    OR UPPER(t.table_name) LIKE '%VAT%'        OR UPPER(t.table_name) LIKE '%GST%'
    OR UPPER(t.table_name) LIKE '%PAYROLL%'    OR UPPER(t.table_name) LIKE '%SALARY%'
    OR UPPER(t.table_name) LIKE '%GATE%'       OR UPPER(t.table_name) LIKE '%EMP%'
     )
ORDER BY t.owner, t.table_name;

PROMPT ============================================================
PROMPT STEP 1B: ALL tables visible to this login (unfiltered), in
PROMPT case the keyword filter above misses this ERP's naming
PROMPT convention - eyeball this list once and refine STEP 1's
PROMPT keywords accordingly.
PROMPT ============================================================

SELECT t.owner, t.table_name, t.num_rows
FROM   all_tables t
WHERE  t.owner NOT IN ('SYS','SYSTEM','SYSMAN','MGMT_VIEW','OUTLN','DBSNMP','APPQOSSYS','XDB','CTXSYS','MDSYS','OLAPSYS','ORDDATA','ORDSYS','ORDPLUGINS','WMSYS','EXFSYS','ANONYMOUS','XS$NULL','SPATIAL_CSW_ADMIN_USR','SPATIAL_WFS_ADMIN_USR','MDDATA','OWBSYS','OWBSYS_AUDIT','SI_INFORMTN_SCHEMA','ORACLE_OCM','APEX_PUBLIC_USER','APEX_030200','FLOWS_FILES','FLOWS_030000','DIP','TSMSYS','SCOTT','HR','OE','PM','IX','SH','BI')
AND    t.table_name NOT LIKE 'SYS\_%' ESCAPE '\'
ORDER BY t.owner, t.table_name;

PROMPT ============================================================
PROMPT STEP 2: Date/Timestamp columns on the STEP 1 candidate tables
PROMPT (this is the working list for backdate-detection queries)
PROMPT ============================================================

SELECT c.owner,
       c.table_name,
       c.column_name,
       c.data_type,
       c.column_id
FROM   all_tab_columns c
WHERE  c.owner NOT IN ('SYS','SYSTEM','SYSMAN','MGMT_VIEW','OUTLN','DBSNMP','APPQOSSYS','XDB','CTXSYS','MDSYS','OLAPSYS','ORDDATA','ORDSYS','ORDPLUGINS','WMSYS','EXFSYS','ANONYMOUS','XS$NULL','SPATIAL_CSW_ADMIN_USR','SPATIAL_WFS_ADMIN_USR','MDDATA','OWBSYS','OWBSYS_AUDIT','SI_INFORMTN_SCHEMA','ORACLE_OCM','APEX_PUBLIC_USER','APEX_030200','FLOWS_FILES','FLOWS_030000','DIP','TSMSYS','SCOTT','HR','OE','PM','IX','SH','BI')
AND    (c.data_type = 'DATE' OR c.data_type LIKE 'TIMESTAMP%')
AND    c.table_name NOT LIKE 'SYS\_%' ESCAPE '\'
AND  (
       UPPER(c.table_name) LIKE '%PRODUCTION%' OR UPPER(c.table_name) LIKE '%PROD%'
    OR UPPER(c.table_name) LIKE '%YARN%'       OR UPPER(c.table_name) LIKE '%SPIN%'
    OR UPPER(c.table_name) LIKE '%WEAV%'       OR UPPER(c.table_name) LIKE '%DYE%'
    OR UPPER(c.table_name) LIKE '%KNIT%'       OR UPPER(c.table_name) LIKE '%FINISH%'
    OR UPPER(c.table_name) LIKE '%FABRIC%'     OR UPPER(c.table_name) LIKE '%GREY%'
    OR UPPER(c.table_name) LIKE '%GRAY%'       OR UPPER(c.table_name) LIKE '%COUNT%'
    OR UPPER(c.table_name) LIKE '%LOT%'        OR UPPER(c.table_name) LIKE '%BATCH%'
    OR UPPER(c.table_name) LIKE '%SHIFT%'      OR UPPER(c.table_name) LIKE '%MACHINE%'
    OR UPPER(c.table_name) LIKE '%LOOM%'       OR UPPER(c.table_name) LIKE '%DISPATCH%'
    OR UPPER(c.table_name) LIKE '%ISSUE%'      OR UPPER(c.table_name) LIKE '%CONSUMPTION%'
    OR UPPER(c.table_name) LIKE '%WASTAGE%'    OR UPPER(c.table_name) LIKE '%INVENTORY%'
    OR UPPER(c.table_name) LIKE '%STOCK%'      OR UPPER(c.table_name) LIKE '%STORE%'
    OR UPPER(c.table_name) LIKE '%PURCHASE%'   OR UPPER(c.table_name) LIKE '%PO\_%' ESCAPE '\'
    OR UPPER(c.table_name) LIKE '%GRN%'        OR UPPER(c.table_name) LIKE '%SALES%'
    OR UPPER(c.table_name) LIKE '%INVOICE%'    OR UPPER(c.table_name) LIKE '%VOUCHER%'
    OR UPPER(c.table_name) LIKE '%PAYMENT%'    OR UPPER(c.table_name) LIKE '%RECEIPT%'
    OR UPPER(c.table_name) LIKE '%LEDGER%'     OR UPPER(c.table_name) LIKE '%JOURNAL%'
    OR UPPER(c.table_name) LIKE '%GL\_%' ESCAPE '\'   OR UPPER(c.table_name) LIKE '%AP\_%' ESCAPE '\'
    OR UPPER(c.table_name) LIKE '%AR\_%' ESCAPE '\'   OR UPPER(c.table_name) LIKE '%COST%'
    OR UPPER(c.table_name) LIKE '%BUDGET%'     OR UPPER(c.table_name) LIKE '%ACCOUNT%'
    OR UPPER(c.table_name) LIKE '%BANK%'       OR UPPER(c.table_name) LIKE '%CHEQUE%'
    OR UPPER(c.table_name) LIKE '%CASH%'       OR UPPER(c.table_name) LIKE '%TAX%'
    OR UPPER(c.table_name) LIKE '%VAT%'        OR UPPER(c.table_name) LIKE '%GST%'
    OR UPPER(c.table_name) LIKE '%PAYROLL%'    OR UPPER(c.table_name) LIKE '%SALARY%'
    OR UPPER(c.table_name) LIKE '%GATE%'       OR UPPER(c.table_name) LIKE '%EMP%'
     )
ORDER BY c.owner, c.table_name, c.column_id;

PROMPT ============================================================
PROMPT STEP 3: BASE TABLES (views excluded) that carry BOTH a system
PROMPT audit-trail timestamp (row creation/feed) AND a user-entered
PROMPT document/transaction date. Prime candidates for backdate
PROMPT detection: compare the audit column (system-stamped, hard to
PROMPT fake) against each document column (user-entered, easy to fake).
PROMPT ============================================================

COLUMN audit_cols    FORMAT A30
COLUMN document_cols FORMAT A60

WITH date_cols AS (
    SELECT c.owner, c.table_name, c.column_name,
           /* This ERP (Forms 6i, Dost Textiles) doesn't use generic Oracle
              EBS-style audit column names - confirmed empirically from Step 2
              output (2026-08-07 run): its actual convention is FEEDD /
              FEEDING_DATE ("date the row was fed/entered into the system")
              and sometimes MODD ("modified date"). Any other date/timestamp
              column on the same table is treated as a candidate user-entered
              "document date" to compare it against. */
           CASE WHEN UPPER(c.column_name) IN
                     ('FEEDD','FEEDING_DATE','MODD',
                      'CREATED_DATE','CREATION_DATE','CREATE_DATE','ROW_CREATED_DATE',
                      'LAST_UPDATE_DATE','SYS_CREATION_DATE','ENTRY_DATE','SYSTEM_DATE')
                THEN 'AUDIT'
                ELSE 'DOCUMENT'
           END AS date_role
    FROM   all_tab_columns c
    WHERE  c.owner NOT IN ('SYS','SYSTEM','SYSMAN','MGMT_VIEW','OUTLN','DBSNMP','APPQOSSYS','XDB','CTXSYS','MDSYS','OLAPSYS','ORDDATA','ORDSYS','ORDPLUGINS','WMSYS','EXFSYS','ANONYMOUS','XS$NULL','SPATIAL_CSW_ADMIN_USR','SPATIAL_WFS_ADMIN_USR','MDDATA','OWBSYS','OWBSYS_AUDIT','SI_INFORMTN_SCHEMA','ORACLE_OCM','APEX_PUBLIC_USER','APEX_030200','FLOWS_FILES','FLOWS_030000','DIP','TSMSYS','SCOTT','HR','OE','PM','IX','SH','BI')
    AND    (c.data_type = 'DATE' OR c.data_type LIKE 'TIMESTAMP%')
    AND    c.table_name NOT LIKE 'SYS\_%' ESCAPE '\'
    AND    EXISTS (SELECT 1 FROM all_tables t2
                   WHERE t2.owner = c.owner AND t2.table_name = c.table_name)
)
SELECT owner, table_name,
       COUNT(CASE WHEN date_role = 'AUDIT'    THEN 1 END) AS audit_date_cols,
       COUNT(CASE WHEN date_role = 'DOCUMENT' THEN 1 END) AS document_date_cols,
       LISTAGG(CASE WHEN date_role = 'AUDIT'    THEN column_name END, ', ')
           WITHIN GROUP (ORDER BY column_name) AS audit_cols,
       LISTAGG(CASE WHEN date_role = 'DOCUMENT' THEN column_name END, ', ')
           WITHIN GROUP (ORDER BY column_name) AS document_cols
FROM   date_cols
GROUP BY owner, table_name
HAVING COUNT(CASE WHEN date_role = 'AUDIT'    THEN 1 END) > 0
AND    COUNT(CASE WHEN date_role = 'DOCUMENT' THEN 1 END) > 0
ORDER BY owner, table_name;

PROMPT ============================================================
PROMPT STEP 4: Summary counts by owner (sanity check / triage)
PROMPT ============================================================

SELECT t.owner, COUNT(*) AS visible_table_count
FROM   all_tables t
WHERE  t.owner NOT IN ('SYS','SYSTEM','SYSMAN','MGMT_VIEW','OUTLN','DBSNMP','APPQOSSYS','XDB','CTXSYS','MDSYS','OLAPSYS','ORDDATA','ORDSYS','ORDPLUGINS','WMSYS','EXFSYS','ANONYMOUS','XS$NULL','SPATIAL_CSW_ADMIN_USR','SPATIAL_WFS_ADMIN_USR','MDDATA','OWBSYS','OWBSYS_AUDIT','SI_INFORMTN_SCHEMA','ORACLE_OCM','APEX_PUBLIC_USER','APEX_030200','FLOWS_FILES','FLOWS_030000','DIP','TSMSYS','SCOTT','HR','OE','PM','IX','SH','BI')
GROUP BY t.owner
ORDER BY visible_table_count DESC;
