/* ============================================================================
   09_broader_prod_object_search.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : 07_wv_daily_prod_lookup.sql's exact-name lookup came back "no
             rows selected" for all 6 candidate names (WV_DAILY_PRD,
             WV_DAILY_PRD_SUM, WV_LOOM_PRD, WV_LOOM_PRD_EFF,
             WV_LOOM_BEAM_WISE_PRD, WV_LOOM_WISE_PRD). Two possible reasons:
             (a) wrong schema - these objects may be owned by a schema not
                 granted to whichever login ran script 07 (try DOSTEX too -
                 STORE/production-side modules tend to live there per the
                 original schema exploration, not DOSTT);
             (b) wrong exact name - those 6 strings came from a block's base
                 table PROPERTY, not a readable "FROM <table>" SQL literal
                 (unlike WV_SET_WISE_PRD.fmx / LOOM_PRD_SET_WISE.fmx, which
                 DID have plain SQL text) - the real name could be close but
                 not identical.
             This script widens the net: wildcard search across ALL owners
             for anything containing PRD, PROD, LOOM, or DAILY, plus a
             synonym check (in case the form queries through a synonym with
             yet another name), plus confirms which user is connected.

   Target  : Oracle Database 11.2.0.1.
   Usage   : Run once per login if the first run comes back empty - try
             DOSTT, then DOSTEX, then any other schema logins available
             (DOSTT_GST, PURI_PAYROLL) - ALL_OBJECTS only shows what the
             connected login owns or has been granted.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN owner        FORMAT A14
COLUMN object_name  FORMAT A30
COLUMN object_type  FORMAT A12
COLUMN table_owner  FORMAT A14

PROMPT ============================================================
PROMPT Connected as:
PROMPT ============================================================
SELECT USER AS connected_as FROM dual;

PROMPT ============================================================
PROMPT STEP 1: Wildcard search - anything (table/view/synonym) whose
PROMPT name contains PRD, PROD, LOOM, or DAILY, visible to this login.
PROMPT ============================================================

SELECT owner, object_name, object_type, status
FROM   all_objects
WHERE  object_type IN ('TABLE','VIEW','SYNONYM','MATERIALIZED VIEW')
AND    (   UPPER(object_name) LIKE '%PRD%'
        OR UPPER(object_name) LIKE '%PROD%'
        OR UPPER(object_name) LIKE '%LOOM%'
        OR UPPER(object_name) LIKE '%DAILY%'
       )
ORDER BY owner, object_type, object_name;

PROMPT ============================================================
PROMPT STEP 2: If any SYNONYMs turned up above, show what they
PROMPT actually point to (a form can query a synonym name that's
PROMPT different again from the real underlying table/view).
PROMPT ============================================================

SELECT owner, synonym_name, table_owner, table_name AS points_to
FROM   all_synonyms
WHERE  UPPER(synonym_name) LIKE '%PRD%'
   OR  UPPER(synonym_name) LIKE '%PROD%'
   OR  UPPER(synonym_name) LIKE '%LOOM%'
   OR  UPPER(synonym_name) LIKE '%DAILY%'
ORDER BY owner, synonym_name;
