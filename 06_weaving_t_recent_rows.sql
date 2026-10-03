/* ============================================================================
   06_weaving_t_recent_rows.sql
   Project : Dost Textiles - Daily Loom Production Email Report
   Purpose : Follow-up to 05_weaving_t_columns.sql. That script's Step 3
             sample row (ROWNUM=1, no ORDER BY) turned out to be an old
             (2016), blank-audit-column row of voucher type
             IGP_SIZED_BEAMS - not representative, and revealed WEAVING_T
             is a voucher/ledger table (VNO/VT/AC/AMT/NARATION), not a
             per-loom production log (no loom number, shift, or meters
             column). Before concluding this table can't support a daily
             loom production report, we need to see:
               (a) what OTHER voucher types (VT) exist besides
                   IGP_SIZED_BEAMS, and how many rows/how recent each is -
                   maybe the daily production entries use a different VT.
               (b) actual RECENT rows (not an arbitrary 2016 one) - to see
                   whether audit columns (USERN/IPADD/FEEDD) are populated
                   today, and what NARATION/AMT look like for current data.

   Target  : Oracle Database 11.2.0.1. Run as DOSTT.
   ============================================================================ */

SET LINESIZE 200
SET PAGESIZE 100
SET FEEDBACK ON

COLUMN vt           FORMAT A22
COLUMN row_count    FORMAT 999,999
COLUMN min_vd       FORMAT A11
COLUMN max_vd       FORMAT A11
COLUMN min_feedd    FORMAT A11
COLUMN max_feedd    FORMAT A11
COLUMN has_feedd    FORMAT 999,999
COLUMN has_usern    FORMAT 999,999

PROMPT ============================================================
PROMPT STEP 1: Every distinct voucher type (VT) in WEAVING_T, with
PROMPT row counts, date range (VD and FEEDD), and how many rows in
PROMPT each type actually have audit columns populated. Tells us
PROMPT which VT(s) represent day-to-day production entries, and
PROMPT whether FEEDD is reliable for them.
PROMPT ============================================================

SELECT vt,
       COUNT(*)          AS row_count,
       MIN(vd)            AS min_vd,
       MAX(vd)            AS max_vd,
       MIN(feedd)         AS min_feedd,
       MAX(feedd)         AS max_feedd,
       COUNT(feedd)       AS has_feedd,
       COUNT(usern)       AS has_usern
FROM   dostt.weaving_t
GROUP BY vt
ORDER BY row_count DESC;

PROMPT ============================================================
PROMPT STEP 2: The 5 most recent rows (ordered by VNO desc - voucher
PROMPT number is sequential/auto-increment, a more reliable recency
PROMPT proxy than VD, which is user-entered and exactly what we're
PROMPT trying to audit). Printed vertically, one column per line,
PROMPT via DBMS_SQL so it works regardless of column types.
PROMPT ============================================================

SET SERVEROUTPUT ON SIZE UNLIMITED

DECLARE
    l_cursor      INTEGER;
    l_desc_tab    DBMS_SQL.DESC_TAB;
    l_col_cnt     INTEGER;
    l_status      INTEGER;
    l_varchar_val VARCHAR2(4000);
    l_number_val  NUMBER;
    l_date_val    DATE;
    l_row_num     PLS_INTEGER := 0;
BEGIN
    l_cursor := DBMS_SQL.OPEN_CURSOR;
    DBMS_SQL.PARSE(l_cursor,
        'SELECT * FROM (SELECT * FROM dostt.weaving_t ORDER BY vno DESC) WHERE ROWNUM <= 5',
        DBMS_SQL.NATIVE);
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

    LOOP
        EXIT WHEN DBMS_SQL.FETCH_ROWS(l_cursor) <= 0;
        l_row_num := l_row_num + 1;
        DBMS_OUTPUT.PUT_LINE('--- Row ' || l_row_num || ' ---');
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
    END LOOP;

    IF l_row_num = 0 THEN
        DBMS_OUTPUT.PUT_LINE('No rows in DOSTT.WEAVING_T.');
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
