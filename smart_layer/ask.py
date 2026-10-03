"""
"Ask the ERP": questions in plain English, Urdu or Roman Urdu, answered by
Claude from the local store.

Claude only gets one tool, run_sql, which runs a single SELECT on a read-only
SQLite connection to the local copy (never the live ERP). Every query it ran
is returned with the answer so the numbers can be checked.

Needs ANTHROPIC_API_KEY (Claude Console). Usage is billed to that account.
"""

import json
import sqlite3
from datetime import date

import anthropic

from . import PRODUCT, schema
from .store import open_read_only

RUN_SQL = {
    "name": "run_sql",
    "description": ("Run one read-only SQLite SELECT (or WITH ... SELECT) on the mill's data and get the rows back as JSON. "
                    "Use it as many times as needed. Results are capped; aggregate in SQL rather than fetching raw rows."),
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "A single SQLite SELECT statement"}},
        "required": ["sql"],
        "additionalProperties": False,
    },
    "strict": True,
}


def system_prompt(cfg):
    company = cfg["company"]
    notes = cfg["ask"].get("notes", "").strip()
    return f"""You are the data assistant of {PRODUCT}, working for {company['name']}, a textile mill.
You answer the owner's and managers' questions about the mill - production, looms, store and stock, purchases,
suppliers, customers, cash, payments and accounts - from the mill's ERP data.

How to work:
- Get every number from the data with the run_sql tool. Never guess or invent figures. If the data cannot answer
  the question, say what is missing.
- The data is a SQLite database with exactly the tables below. Dates are ISO text ('YYYY-MM-DD'); use date(),
  strftime() and julianday() for date maths. Money is in {company['currency']}.
- If a question leaves the period open, pick the obvious one (e.g. "this month") and say which period you used.
- Efficiency of a row is COALESCE(efficiency_pct, 100.0 * meters / meters_at_100). Combined efficiency (several looms,
  shifts or days) is weighted by meters_at_100: SUM(eff * meters_at_100) / SUM(meters_at_100).
- Reply in the language and script the user wrote in (English, Urdu, or Roman Urdu). Keep answers short and
  practical: the answer first, then a small table or a few lines of detail if useful.

{schema.describe()}
{('Notes about this mill and its ERP:' + chr(10) + notes) if notes else ''}"""


class Assistant:
    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.client = client or anthropic.Anthropic()
        self.db_path = cfg.path(cfg["store"]["path"])
        self.system = [{"type": "text", "text": system_prompt(cfg), "cache_control": {"type": "ephemeral"}}]
        self.messages = []          # kept append-only across questions so the conversation stays cached

    def run_sql(self, sql):
        a = self.cfg["ask"]
        con = open_read_only(self.db_path)
        try:
            body = sql.strip().rstrip(";")
            if ";" in body:
                raise sqlite3.ProgrammingError("one statement only")
            cur = con.execute(body)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(int(a["max_rows"]) + 1)
        finally:
            con.close()
        more = len(rows) > int(a["max_rows"])
        return json.dumps({"columns": cols, "rows": rows[: int(a["max_rows"])], "truncated": more}, default=str)

    def ask(self, question, on_sql=None):
        """Returns (answer_text, [sql, ...])."""
        a = self.cfg["ask"]
        synced = self._last_sync()
        self.messages.append({"role": "user", "content": f"[Today is {date.today():%Y-%m-%d}; data synced {synced}]\n{question}"})
        queries = []
        for _ in range(int(a["max_steps"])):
            response = self.client.beta.messages.create(
                model=a["model"],
                max_tokens=16000,
                system=self.system,
                tools=[RUN_SQL],
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": a["effort"]},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            self.messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                return "Sorry, I can't help with that request.", queries
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n[answer cut short]"
                return text, queries
            results = []
            for call in calls:
                sql = call.input.get("sql", "") if isinstance(call.input, dict) else ""
                queries.append(sql)
                if on_sql:
                    on_sql(sql)
                try:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": self.run_sql(sql)})
                except sqlite3.Error as e:
                    results.append({"type": "tool_result", "tool_use_id": call.id, "content": f"SQL error: {e}", "is_error": True})
            self.messages.append({"role": "user", "content": results})
        return "I could not finish this answer within the step limit - try a narrower question.", queries

    def _last_sync(self):
        try:
            con = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            row = con.execute("SELECT MAX(synced_at) FROM sync_log").fetchone()
            con.close()
            return row[0] or "never"
        except sqlite3.Error:
            return "unknown"
