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
    Entity(
        "yarn_stock",
        "Count-wise loose-yarn stock on hand, as a snapshot at sync time. 'weft' = the mill's own loose yarn; "
        "'sizing' = yarn out at a sizing party (sent minus received, plus the sizer's opening). Quantities are net "
        "and can be negative. Everything is carried as total cones (qty_cones); bags and loose cones are derived "
        "as TRUNC(qty_cones / cone_pbag) and the sign-preserving remainder.",
        (C("section", "TEXT", "'weft' (in the mill) or 'sizing' (out at a sizer)", True),
         C("party_name", "TEXT", "Sizing party (set only for the 'sizing' section)"),
         C("count", "TEXT", "Yarn count / description (e.g. 52/1 CTN)", True),
         C("brand", "TEXT", "Yarn brand (e.g. NISHAT)", True),
         C("cone_pbag", "REAL", "Cones per bag for this packing (part of the grouping key)", True),
         C("qty_cones", "REAL", "Net stock in total cones (bags x cone_pbag + loose cones)", True)),
    ),
    Entity(
        "fabric_stock",
        "Fabric in stock exactly as the ERP's own fabric-stock report shows it (opening + woven - dispatched + "
        "returns), per contract and quality, as a snapshot at sync time. Meters are net and a row can be negative - "
        "that is a data flag (dispatched more than produced for that grade), not real negative fabric.",
        (C("contract_no", "TEXT", "Contract", True),
         C("party_name", "TEXT", "Party"),
         C("quality", "TEXT", "Fabric quality", True),
         C("fresh_m", "REAL", "Fresh (first quality) meters in stock", True),
         C("b_m", "REAL", "B-grade meters"),
         C("c_m", "REAL", "C-grade meters"),
         C("cp_m", "REAL", "CP-grade meters"),
         C("meters", "REAL", "Total meters in stock (net, as the ERP reports it)", True)),
    ),
)

# Payroll and the gate register. Payroll usually lives in its own ERP schema; the gate register is one list of
# every gate pass in and out of the mill. Both are mapped read-only like the rest.
ENTITIES += (
    Entity(
        "employees",
        "Employee master from the payroll system: who they are, where they work and their pay rate.",
        (C("emp_code", "TEXT", "Employee code", True),
         C("name", "TEXT", "Employee name", True),
         C("father_name", "TEXT", "Father / husband name"),
         C("cnic", "TEXT", "National ID (CNIC)"),
         C("department", "TEXT", "Department"),
         C("sub_department", "TEXT", "Sub-department"),
         C("section", "TEXT", "Section (e.g. admin, factory)"),
         C("designation", "TEXT", "Designation / job title"),
         C("job_type", "TEXT", "Job type (permanent, contract, daily ...)"),
         C("shift", "TEXT", "Shift"),
         C("segment", "TEXT", "Unit / segment of the group"),
         C("date_joining", "DATE", "Date of joining"),
         C("status", "TEXT", "Employment status (active, left ...)"),
         C("wage_type", "TEXT", "How pay is set (monthly, daily, piece ...)"),
         C("monthly_salary", "REAL", "Monthly salary / rate"),
         C("per_day_wage", "REAL", "Per-day wage, if daily-rated"),
         C("bank", "TEXT", "Bank the salary is paid to"),
         C("bank_account", "TEXT", "Bank account number"),
         C("mobile", "TEXT", "Mobile / phone"),
         C("eobi_no", "TEXT", "EOBI number, if registered"),
         C("social_security_no", "TEXT", "Social security number, if registered")),
        key=("emp_code",),
    ),
    Entity(
        "payroll",
        "One salary record per employee per pay period. Mills often pay twice a month, so a period can be half a month. "
        "Net pay is what the employee is actually paid: earned for the days worked, plus overtime, minus deductions.",
        (C("emp_code", "TEXT", "Employee code (employees.emp_code)", True),
         C("period_start", "DATE", "First day of the pay period", True),
         C("period_end", "DATE", "Last day of the pay period", True),
         C("pay_month", "TEXT", "Calendar month the period belongs to (YYYY-MM)"),
         C("section", "TEXT", "Section (admin / factory ...) for this pay run"),
         C("designation", "TEXT", "Designation at the time"),
         C("worked_days", "REAL", "Days worked in the period"),
         C("monthly_rate", "REAL", "Monthly salary rate (a reference, not what is paid)"),
         C("earned", "REAL", "Pay earned for the days worked in this period", True),
         C("overtime", "REAL", "Overtime pay in this period"),
         C("deductions", "REAL", "Deductions in this period (loan, advance, mess ...)"),
         C("net_pay", "REAL", "Net pay = earned + overtime - deductions", True),
         C("paid", "TEXT", "Whether it has been paid")),
        date_column="period_end",
        key=("emp_code", "period_start", "period_end"),
    ),
    Entity(
        "employee_loans",
        "Loans and advances given to employees, recovered from salary over time.",
        (C("loan_code", "TEXT", "Loan / advance reference", True),
         C("loan_date", "DATE", "Date given", True),
         C("emp_code", "TEXT", "Employee (employees.emp_code)", True),
         C("loan_type", "TEXT", "Loan / advance / other"),
         C("amount", "REAL", "Amount given"),
         C("installment", "REAL", "Agreed recovery per period"),
         C("opening", "REAL", "Opening balance brought forward"),
         C("completed", "TEXT", "Whether fully recovered"),
         C("remarks", "TEXT", "Remarks")),
        date_column="loan_date",
        key=("loan_code",),
    ),
    Entity(
        "gate_passes",
        "The gate register: every gate pass in and out of the mill - fabric, yarn, beams, store - one row per item line. "
        "Numbers repeat across types and over time, so each slip is identified by gatepass_id, not the bare number.",
        (C("gatepass_id", "TEXT", "Unique slip id (type + number + date), so same-numbered passes never clash", True),
         C("gp_no", "TEXT", "Gate pass number as written on the slip", True),
         C("gp_date", "DATE", "Gate pass date", True),
         C("direction", "TEXT", "'in' = came into the mill, 'out' = left the mill", True),
         C("type", "TEXT", "Kind of gate pass (OGP fabric, IGP yarn, store GRN, empty beam to sizer ...)", True),
         C("party_code", "TEXT", "Party (parties.party_code)"),
         C("party_name", "TEXT", "Party name"),
         C("vehicle", "TEXT", "Vehicle number"),
         C("driver", "TEXT", "Driver"),
         C("item_code", "TEXT", "Item / yarn / beam code"),
         C("item", "TEXT", "Item description"),
         C("qty", "REAL", "Quantity on this line"),
         C("uom", "TEXT", "Unit"),
         C("contract_no", "TEXT", "Contract, when the pass is tied to one"),
         C("beam_no", "TEXT", "Beam number, for beam gate passes"),
         C("entered_by", "TEXT", "ERP user who entered it")),
        date_column="gp_date",
        key=("gatepass_id", "item_code"),
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
