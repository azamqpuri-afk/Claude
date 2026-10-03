/* ============================================================================
   08_check_shared_pool_history.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : Best-effort check of V$SQL for any recently-run query that looks
             like the Daily Production report (LOOM/PROD keywords). Only
             useful if the report was actually run recently AND hasn't been
             aged out of the shared pool AND this login has access to V$SQL.

   Caveat  : V$ views require SELECT_CATALOG_ROLE or a DBA-granted SELECT ON
             V_$SQL. The app schema logins we've been using (DOSTT, DOSTEX,
             etc.) are plain schema owners, not DBA - this will very likely
             fail with ORA-00942 "table or view does not exist" for them.
             Try it, but don't spend time on it if it errors - Step 1-3 of
             07_wv_daily_prod_lookup.sql (real DB objects) is the reliable
             path, this is just a bonus if it happens to work.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN sql_text     FORMAT A150
COLUMN first_load   FORMAT A20
COLUMN last_active   FORMAT A20

SELECT sql_text,
       first_load_time AS first_load,
       last_active_time AS last_active,
       executions
FROM   v$sql
WHERE  (UPPER(sql_text) LIKE '%LOOM%' AND UPPER(sql_text) LIKE '%PROD%')
   OR   UPPER(sql_text) LIKE '%WV_DAILY_PRD%'
   OR   UPPER(sql_text) LIKE '%WV_LOOM_PRD%'
ORDER BY last_active_time DESC;
