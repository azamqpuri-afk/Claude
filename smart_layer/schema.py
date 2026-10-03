"""
The standard (canonical) data model.

Every ERP is mapped onto these entities by a mapping file (see mappings/).
Reports, audits and "ask the ERP" are written only against these entities,
so they work unchanged for any ERP once its mapping exists.

This module is the single source of truth: it creates the local store's
tables, checks that a mapping returns the right columns, and is the schema
description Claude reads when answering questions.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    type: str            # TEXT | REAL | DATE | DATETIME
    description: str
    required: bool = False


@dataclass(frozen=True)
class Entity:
    name: str
    description: str
    columns: tuple
    date_column: str | None = None     # set = synced as a rolling window on this date
    key: tuple = field(default_factory=tuple)

    @property
    def column_names(self):
        return [c.name for c in self.columns]


C = Column

ACCOUNT_GROUPS = ("cash", "bank", "customer", "supplier", "revenue", "expense",
                  "asset", "liability", "equity", "other")
VOUCHER_KINDS = ("payment", "receipt", "journal", "sale", "purchase", "opening", "other")
PARTY_TYPES = ("customer", "supplier", "sizer", "employee", "other")

ENTITIES = (
    Entity(
        "items",
        "Store / inventory item master (spares, chemicals, packing, yarn bought for the store).",
        (C("item_code", "TEXT", "Item code as used in the ERP", True),
         C("item_name", "TEXT", "Item description", True),
         C("unit", "TEXT", "Unit of measure (pcs, kg, ltr, m ...)"),
         C("category", "TEXT", "Item group / category, if the ERP has one")),
        key=("item_code",),
    ),
    Entity(
        "stock_balances",
        "Current stock quantity of each item (a snapshot taken at sync time).",
        (C("item_code", "TEXT", "Item code (items.item_code)", True),
         C("qty", "REAL", "Quantity in stock now; negative means the ERP is wrong", True),
         C("location", "TEXT", "Store / godown, if the ERP tracks it")),
    ),
    Entity(
        "store_receipts",
        "Goods received into the store (GRN / purchase receipt lines).",
        (C("doc_no", "TEXT", "Receipt (GRN) number", True),
         C("doc_date", "DATE", "Date on the receipt", True),
         C("entered_at", "DATETIME", "When the line was keyed into the ERP"),
         C("item_code", "TEXT", "Item code", True),
         C("qty", "REAL", "Quantity received", True),
         C("rate", "REAL", "Price per unit"),
         C("amount", "REAL", "Line value"),
         C("supplier_code", "TEXT", "Supplier code / account"),
         C("supplier_name", "TEXT", "Supplier name"),
         C("supplier_bill_no", "TEXT", "Supplier's own bill / invoice number"),
         C("entered_by", "TEXT", "ERP user who keyed it")),
        date_column="doc_date",
    ),
    Entity(
        "store_issues",
        "Items issued out of the store (issue slip lines).",
        (C("doc_no", "TEXT", "Issue slip number", True),
         C("doc_date", "DATE", "Date on the slip", True),
         C("entered_at", "DATETIME", "When the line was keyed into the ERP"),
         C("item_code", "TEXT", "Item code", True),
         C("qty", "REAL", "Quantity issued", True),
         C("amount", "REAL", "Line value"),
         C("issued_to", "TEXT", "Department / machine / loom it went to"),
         C("entered_by", "TEXT", "ERP user who keyed it")),
        date_column="doc_date",
    ),
    Entity(
        "parties",
        "Customers, suppliers, sizing parties and other business partners.",
        (C("party_code", "TEXT", "Party code", True),
         C("party_name", "TEXT", "Party name", True),
         C("party_type", "TEXT", "One of: " + ", ".join(PARTY_TYPES))),
        key=("party_code",),
    ),
    Entity(
        "accounts",
        "Chart of accounts.",
        (C("account_code", "TEXT", "Account code", True),
         C("account_name", "TEXT", "Account name", True),
         C("account_group", "TEXT", "One of: " + ", ".join(ACCOUNT_GROUPS), True),
         C("party_code", "TEXT", "Linked party, for customer / supplier accounts")),
        key=("account_code",),
    ),
    Entity(
        "ledger_entries",
        "General ledger lines. amount is signed: debit positive, credit negative, "
        "so every voucher's lines sum to zero.",
        (C("voucher_id", "TEXT", "Unique key of the voucher (lines of one voucher share it)", True),
         C("voucher_no", "TEXT", "Voucher number as printed", True),
         C("voucher_type", "TEXT", "The ERP's own voucher type code (e.g. CP, BP, JV)", True),
         C("voucher_kind", "TEXT", "One of: " + ", ".join(VOUCHER_KINDS), True),
         C("voucher_date", "DATE", "Date of the voucher", True),
         C("entered_at", "DATETIME", "When it was keyed into the ERP"),
         C("account_code", "TEXT", "Account (accounts.account_code)", True),
         C("amount", "REAL", "Debit positive, credit negative", True),
         C("narration", "TEXT", "Narration / description"),
         C("entered_by", "TEXT", "ERP user who keyed it")),
        date_column="voucher_date",
    ),
    Entity(
        "account_balances",
        "Balance of every account over the whole ledger, now (debit positive). "
        "Their total is the trial balance difference and should be zero.",
        (C("account_code", "TEXT", "Account code", True),
         C("balance", "REAL", "Debit positive, credit negative", True)),
        key=("account_code",),
    ),
    Entity(
        "looms",
        "Weaving machines.",
        (C("loom_code", "TEXT", "Loom code", True),
         C("loom_name", "TEXT", "Loom name / number", True),
         C("shed", "TEXT", "Shed / hall, if recorded")),
        key=("loom_code",),
    ),
    Entity(
        "loom_production",
        "Woven fabric per loom per shift (one row per loom, date, shift and quality).",
        (C("prod_date", "DATE", "Production date", True),
         C("entered_at", "DATETIME", "When it was keyed into the ERP"),
         C("loom_code", "TEXT", "Loom (looms.loom_code)", True),
         C("shift", "TEXT", "Shift (A, B, C, Day, Night ...)"),
         C("contract_no", "TEXT", "Sales contract / order the fabric is for"),
         C("quality", "TEXT", "Fabric quality / construction"),
         C("beam_no", "TEXT", "Warp beam on the loom, if the ERP records it"),
         C("meters", "REAL", "Total meters woven", True),
         C("first_quality_m", "REAL", "Meters of first (A) grade"),
         C("second_quality_m", "REAL", "Meters of second (B) grade"),
         C("rejected_m", "REAL", "Meters rejected (C / cut pieces)"),
         C("efficiency_pct", "REAL", "Efficiency as calculated by the ERP, if it has one"),
         C("meters_at_100", "REAL", "Meters the loom would weave at 100% efficiency")),
        date_column="prod_date",
    ),
)

BY_NAME = {e.name: e for e in ENTITIES}


def describe(entities=ENTITIES):
    """Plain-text schema description (used in Claude's system prompt and the docs)."""
    out = []
    for e in entities:
        out.append(f"TABLE {e.name} -- {e.description}")
        for c in e.columns:
            out.append(f"  {c.name} {c.type}{' NOT NULL' if c.required else ''} -- {c.description}")
        out.append("")
    return "\n".join(out)
