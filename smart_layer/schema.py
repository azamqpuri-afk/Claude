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
         C("party_code", "TEXT", "Linked party, for customer / supplier accounts"),
         C("head_name", "TEXT", "Account head / group it rolls up to in the P&L or balance sheet (e.g. Wages, Power)")),
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
         C("rejected_m", "REAL", "Meters counted as rejection, as the mill's ERP defines it (e.g. C + cut pieces, or B + C + CP)"),
         C("efficiency_pct", "REAL", "Efficiency as calculated by the ERP, if it has one"),
         C("meters_at_100", "REAL", "Meters the loom would weave at 100% efficiency")),
        date_column="prod_date",
    ),
    Entity(
        "contracts",
        "Weaving contracts / sales orders, with the fabric construction needed to work out yarn per meter.",
        (C("contract_no", "TEXT", "Contract / order number", True),
         C("party_code", "TEXT", "Customer / yarn owner (parties.party_code)"),
         C("party_name", "TEXT", "Customer name"),
         C("contract_date", "DATE", "Contract date"),
         C("quality", "TEXT", "Fabric quality / construction"),
         C("ends_per_inch", "REAL", "Warp ends per inch"),
         C("picks_per_inch", "REAL", "Weft picks per inch"),
         C("width_in", "REAL", "Fabric (reed) width in inches"),
         C("warp_yarn", "TEXT", "Warp yarn code (yarn_movements.yarn_code)"),
         C("warp_count", "REAL", "Warp yarn count (English cotton count, Ne)"),
         C("weft_yarn", "TEXT", "Weft yarn code"),
         C("weft_count", "REAL", "Weft yarn count (Ne)"),
         C("ordered_meters", "REAL", "Contract quantity in meters"),
         C("rate_per_meter", "REAL", "Conversion / sale rate per meter"),
         C("rate_per_pick", "REAL", "Conversion rate per pick (value per meter = picks per inch x rate per pick)"),
         C("yarn_owner", "TEXT", "'party' when the customer supplies the yarn (conversion), 'mill' when the mill buys it")),
        key=("contract_no",),
    ),
    Entity(
        "yarn_movements",
        "Every yarn movement: received from or returned to a party, sent to / back from a sizer, sized beams received, "
        "issued to / returned from looms (weft), sold, opening stock. Quantities are positive; move_type gives the direction.",
        (C("move_date", "DATE", "Date of the movement", True),
         C("entered_at", "DATETIME", "When it was keyed into the ERP"),
         C("doc_no", "TEXT", "Gate pass / document number"),
         C("move_type", "TEXT", "One of: " + ", ".join(
             ("party_receipt", "party_return", "sale", "to_sizer", "from_sizer", "sized_beams_received",
              "to_looms", "from_looms", "opening", "other")), True),
         C("yarn_code", "TEXT", "Yarn code", True),
         C("yarn_name", "TEXT", "Yarn description (e.g. 40/1 CTN)"),
         C("contract_no", "TEXT", "Contract the yarn belongs to"),
         C("party_code", "TEXT", "Party (yarn owner for receipts/returns, the sizer for sizing movements)"),
         C("party_name", "TEXT", "Party name"),
         C("bags", "REAL", "Bags"),
         C("kg", "REAL", "Weight in kg", True),
         C("entered_by", "TEXT", "ERP user who keyed it")),
        date_column="move_date",
    ),
    Entity(
        "fabric_dispatches",
        "Fabric delivered to parties (direction 'out') and fabric returned by them (direction 'in').",
        (C("doc_no", "TEXT", "Gate pass / invoice number", True),
         C("dispatch_date", "DATE", "Date", True),
         C("contract_no", "TEXT", "Contract", True),
         C("party_code", "TEXT", "Party"),
         C("direction", "TEXT", "'out' = delivered to the party, 'in' = returned to the mill", True),
         C("fresh_m", "REAL", "Fresh (first quality) meters"),
         C("rejected_m", "REAL", "Rejected / second quality meters"),
         C("meters", "REAL", "Total meters", True),
         C("amount", "REAL", "Invoice value")),
        date_column="dispatch_date",
    ),
)

ENTITIES += (
    Entity(
        "beams",
        "Every warp beam now: on a loom, sized and waiting, empty in the mill, or at a sizer (a snapshot at sync time).",
        (C("beam_no", "TEXT", "Beam number painted on the beam", True),
         C("status", "TEXT", "One of: on_loom, sized_waiting, empty_in_mill, at_sizer, sent_out, other", True),
         C("set_no", "TEXT", "Sizing set number"),
         C("contract_no", "TEXT", "Contract the yarn on it belongs to"),
         C("loom_code", "TEXT", "Loom it is on (looms.loom_code)"),
         C("sizer_name", "TEXT", "Sizer that made it / holds it"),
         C("ends", "REAL", "Total warp ends on the set"),
         C("set_length_m", "REAL", "Warp length on the beam when sized (meters)"),
         C("woven_m", "REAL", "Fabric woven from this beam so far (meters)"),
         C("length_left_m", "REAL", "Warp length still on the beam, if the ERP tracks it"),
         C("mounted_date", "DATE", "Date put on the loom"),
         C("received_date", "DATE", "Date received back from the sizer")),
        key=("beam_no",),
    ),
    Entity(
        "beam_movements",
        "Every beam movement: empty beams to / from a sizer, sized beams received or sent out, beams put on / taken off looms.",
        (C("move_date", "DATE", "Date", True),
         C("doc_no", "TEXT", "Gate pass / document number"),
         C("beam_no", "TEXT", "Beam number", True),
         C("move_type", "TEXT", "One of: empty_to_sizer, empty_from_sizer, sized_received, sized_sent_out, to_loom, "
                                "from_loom_finished, from_loom_cut, other", True),
         C("party_name", "TEXT", "Sizer / party"),
         C("set_no", "TEXT", "Sizing set number"),
         C("contract_no", "TEXT", "Contract"),
         C("loom_code", "TEXT", "Loom"),
         C("length_m", "REAL", "Set length on the beam (for sized beams)"),
         C("woven_m", "REAL", "Fabric woven from the beam (when taken off a loom), if known")),
        date_column="move_date",
    ),
    Entity(
        "loom_monitor",
        "Readings from a loom monitoring system (scoreboard / sensors) per loom per day, independent of the ERP.",
        (C("read_date", "DATE", "Date", True),
         C("loom_code", "TEXT", "Loom (looms.loom_code)", True),
         C("meters", "REAL", "Meters counted by the monitor", True),
         C("efficiency_pct", "REAL", "Efficiency by the monitor"),
         C("rpm", "REAL", "Average speed"),
         C("stops", "REAL", "Number of stops")),
        date_column="read_date",
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
