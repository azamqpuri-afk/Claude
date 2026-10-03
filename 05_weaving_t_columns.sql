/* ============================================================================
   05_weaving_t_columns.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : Full column inventory for DOSTT.WEAVING_T, so we can pick which
             columns belong in a daily loom production report (quantity,
             loom number, quality/style, shift, meters, etc.) before writing
             any report/email code against it.

   Target  : Oracle Database 11.2.0.1. Run as DOSTT (or any login with
             SELECT on DOSTT.WEAVING_T) in SQL*Plus / PL-SQL Developer.

   Output  : Step 1 - every column, in table order, with type/length/nullability.
             Step 2 - row count and MIN/MAX of every DATE column found, so we
                      know which column represents "today's production" and
                      how much history exists.
             Step 3 - one sample row (top of table, arbitrary order) printed
                      vertically (one column per line) so long/many columns
                      are actually readable - this is the fastest way to see
                      real values (units, code lookups, free text, etc.)
                      without guessing from column names alone.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN column_name  FORMAT A32
COLUMN data_type    FORMAT A15
COLUMN data_length  FORMAT 9999
COLUMN nullable     FORMAT A8

PROMPT ============================================================
PROMPT STEP 1: Full column list for DOSTT.WEAVING_T, in table order
PROMPT ============================================================

SELECT column_id,
       column_name,
       data_type
       || CASE WHEN data_type IN ('VARCHAR2','CHAR') THEN '(' || data_length || ')'
               WHEN data_type = 'NUMBER' AND data_precision IS NOT NULL
                    THEN '(' || data_precision || ',' || NVL(data_scale,0) || ')'
               ELSE NULL END AS data_type,
       data_length,
       DECODE(nullable, 'Y', 'YES', 'NO') AS nullable
FROM   all_tab_columns
WHERE  owner = 'DOSTT'
AND    table_name = 'WEAVING_T'
ORDER BY column_id;

PROMPT ============================================================
PROMPT STEP 2: Row count, and MIN/MAX of every DATE column on the
PROMPT table (helps identify which column = "production date" and
PROMPT how much history is available).
PROMPT ============================================================

SELECT COUNT(*) AS total_rows FROM dostt.weaving_t;

SELECT column_name, data_type
FROM   all_tab_columns
WHERE  owner = 'DOSTT'
AND    table_name = 'WEAVING_T'
AND    (data_type = 'DATE' OR data_type LIKE 'TIMESTAMP%')
ORDER BY column_id;

/* Run this by hand for each date column Step 2 above lists, e.g.:
     SELECT MIN(feedd) AS min_val, MAX(feedd) AS max_val, COUNT(feedd) AS non_null
     FROM   dostt.weaving_t;
   (kept manual rather than dynamic SQL - column names vary and this table's
   real date-column names aren't known yet; fill in once Step 1's output is read) */

PROMPT ============================================================
PROMPT STEP 3: One sample row, printed vertically (one column per
PROMPT line) so every value is actually readable regardless of how
PROMPT many columns the table has. Uses DBMS_SQL (not UNPIVOT *,
PROMPT which needs 12.2+ and this instance is 11.2.0.1) so it works
PROMPT without knowing the column list or types ahead of time.
PROMPT ============================================================

SET SERVEROUTPUT ON SIZE UNLIMITED
SET PAGESIZE 100

DECLARE
    l_cursor      INTEGER;
    l_desc_tab    DBMS_SQL.DESC_TAB;
    l_col_cnt     INTEGER;
    l_status      INTEGER;
    l_varchar_val VARCHAR2(4000);
    l_number_val  NUMBER;
    l_date_val    DATE;
BEGIN
    l_cursor := DBMS_SQL.OPEN_CURSOR;
    DBMS_SQL.PARSE(l_cursor, 'SELECT * FROM dostt.weaving_t WHERE ROWNUM = 1', DBMS_SQL.NATIVE);
    DBMS_SQL.DESCRIBE_COLUMNS(l_cursor, l_col_cnt, l_desc_tab);

    FOR i IN 1 .. l_col_cnt LOOP
        IF l_desc_tab(i).col_type = 2 THEN        -- NUMBER
            DBMS_SQL.DEFINE_COLUMN(l_cursor, i, l_number_val);
        ELSIF l_desc_tab(i).col_type = 12 THEN     -- DATE
            DBMS_SQL.DEFINE_COLUMN(l_cursor, i, l_date_val);
        ELSE                                       -- VARCHAR2/CHAR/everything else
            DBMS_SQL.DEFINE_COLUMN(l_cursor, i, l_varchar_val, 4000);
        END IF;
    END LOOP;

    l_status := DBMS_SQL.EXECUTE(l_cursor);

    IF DBMS_SQL.FETCH_ROWS(l_cursor) > 0 THEN
        FOR i IN 1 .. l_col_cnt LOOP
            BEGIN
                IF l_desc_tab(i).col_type = 2 THEN
                    DBMS_SQL.COLUMN_VALUE(l_cursor, i, l_number_val);
                    DBMS_OUTPUT.PUT_LINE(RPAD(l_desc_tab(i).col_name, 32) || ': ' || TO_CHAR(l_number_val));
                ELSIF l_desc_tab(i).col_type = 12 THEN
                    DBMS_SQL.COLUMN_VALUE(l_cursor, i, l_date_val);
                    DBMS_OUTPUT.PUT_LINE(RPAD(l_desc_tab(i).col_name, 32) || ': ' || TO_CHAR(l_date_val, 'DD-MON-YYYY HH24:MI:SS'));
                ELSE
                    DBMS_SQL.COLUMN_VALUE(l_cursor, i, l_varchar_val);
                    DBMS_OUTPUT.PUT_LINE(RPAD(l_desc_tab(i).col_name, 32) || ': ' || l_varchar_val);
                END IF;
            EXCEPTION WHEN OTHERS THEN
                DBMS_OUTPUT.PUT_LINE(RPAD(l_desc_tab(i).col_name, 32) || ': (unreadable/NULL - ' || SQLERRM || ')');
            END;
        END LOOP;
    ELSE
        DBMS_OUTPUT.PUT_LINE('No rows in DOSTT.WEAVING_T - table is empty.');
    END IF;

    DBMS_SQL.CLOSE_CURSOR(l_cursor);
EXCEPTION
    WHEN OTHERS THEN
        IF DBMS_SQL.IS_OPEN(l_cursor) THEN
            DBMS_SQL.CLOSE_CURSOR(l_cursor);
        END IF;
        RAISE;
END;
/
