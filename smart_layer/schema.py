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
         C("category", "TEXT", "Item group / category, if the ERP has one"),
         C("head", "TEXT", "Item head (top group, e.g. Loom Parts, Electrical), if the ERP has one (item 30e)"),
         C("sub_head", "TEXT", "Item sub-head (second-level group), if the ERP has one")),
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
        "store_returns",
        "Items returned INTO the store (return slip lines) - used to net store expense (issues - returns). "
        "Optional: only mills that record store returns separately map it (e.g. Dost's DMN type 'RET').",
        (C("doc_no", "TEXT", "Return slip number", True),
         C("doc_date", "DATE", "Date on the slip", True),
         C("entered_at", "DATETIME", "When the line was keyed into the ERP"),
         C("item_code", "TEXT", "Item code", True),
         C("qty", "REAL", "Quantity returned", True),
         C("amount", "REAL", "Line value"),
         C("returned_from", "TEXT", "Department / machine / loom it came back from"),
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
         C("quality", "TEXT", "Sub-quality of the cloth on this gate pass (a contract can run more than one)"),
         C("party_code", "TEXT", "Account the gate pass is billed to (the contract party, or a mill rejection store)"),
         C("deliver_to", "TEXT", "Deliver-to text on the gate pass"),
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
         C("received_date", "DATE", "Date received back from the sizer"),
         C("bwidth", "REAL", "Panels across the beam (3 for a 3-panel beam, 1 otherwise) - set length x bwidth = fabric length")),
        key=("beam_no",),
    ),
    Entity(
        "empty_beam_stock",
        "Empty beams out at each sizer, from the ERP's own empty-beam-stock view (+1 per empty sent, -1 per empty or "
        "sized beam back). One row per sizer.",
        (C("party_name", "TEXT", "Sizer the empty beams are with", True),
         C("beams", "REAL", "Net number of empty beams currently with this sizer")),
        key=("party_name",),
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
        "sized_sets",
        "One row per sized warp set received from a sizer (the sizing IGP), with everything the sized-yarn closing "
        "count needs: ends, set length, yarn sent, sized weight, gullay (returned baby cones) and waste. Optional - "
        "mills that record sized-beam receipts in detail map it (e.g. Dost's WYTD sized-beam IGP). Smart Layer "
        "manual inputs override any field per set when the ERP value is missing or corrected.",
        (C("set_no", "TEXT", "Sizing set number", True),
         C("set_date", "DATE", "Date the sized set was received", True),
         C("sizer_name", "TEXT", "Sizer the set came from"),
         C("contract_no", "TEXT", "Contract the set is for"),
         C("ends", "REAL", "Total ends in the set"),
         C("set_length_m", "REAL", "Set length (metres)"),
         C("bags", "REAL", "Bags of yarn used for the set"),
         C("cones_per_bag", "REAL", "Cones per bag"),
         C("loose_cones", "REAL", "Loose cones (not in a full bag)"),
         C("gullay_kg", "REAL", "Gullay: weight of yarn left on returned baby cones (kg)"),
         C("waste_kg", "REAL", "Sizing waste (kg)"),
         C("yarn_sent_kg", "REAL", "Yarn sent to the sizer for the set (kg)"),
         C("sized_kg", "REAL", "Sized-beam weight received (kg, including size paste)")),
        date_column="set_date",
    ),
    Entity(
        "loom_monitor",
        "Readings from a loom monitoring system (scoreboard / sensors) per loom per day, independent of the ERP.",
        (C("read_date", "DATE", "Date", True),
         C("loom_code", "TEXT", "Loom (looms.loom_code)", True),
         C("shift", "TEXT", "Shift (day / night, or the monitor's code) when the monitor reports per shift; "
                            "blank when it only gives a whole-day figure (then day/night cannot be split)"),
         C("meters", "REAL", "Meters counted by the monitor", True),
         C("efficiency_pct", "REAL", "Efficiency by the monitor"),
         C("rpm", "REAL", "Average speed"),
         C("stops", "REAL", "Number of stops"),
         C("shift_sec", "REAL", "Total shift time in this reading (seconds) - the weight for a time-based efficiency")),
        date_column="read_date",
    ),
    Entity(
        "loom_monitor_live",
        "The loom monitor's CURRENT (running) shift, one row per loom, as the scoreboard screen shows it right now "
        "- a live snapshot replaced every sync (unlike loom_monitor, which a monitor writes only at shift end). "
        "Optional: mills whose monitor exposes a live per-shift table map it; the Overview scoreboard-efficiency "
        "tile reads it for the running shift and falls back to loom_monitor's last day when it is absent. "
        "Current-shift efficiency = sum(elapsed - stopped) / sum(elapsed) across the running looms.",
        (C("loom_code", "TEXT", "Loom (looms.loom_code)", True),
         C("shift", "TEXT", "Shift number / name of the running shift"),
         C("shift_start", "DATETIME", "When the running shift started"),
         C("elapsed_sec", "REAL", "Seconds elapsed in the shift so far"),
         C("stopped_sec", "REAL", "Seconds the loom was stopped so far this shift"),
         C("stops", "REAL", "Number of stops this shift"),
         C("picks", "REAL", "Picks inserted this shift (production; 0 = idle)"),
         C("deliveries", "REAL", "Fabric widths woven side by side (the delivery factor for metres)"),
         C("rpm", "REAL", "Current speed"),
         C("picks_per_inch", "REAL", "Picks per inch being woven"),
         C("quality", "TEXT", "Quality / article on the loom"),
         C("updated_at", "DATETIME", "When the monitor last updated this row")),
        key=("loom_code",),
    ),
    Entity(
        "loom_stops",
        "Raw per-stop log from a loom monitoring system (scoreboard / sensors): one row per loom stop, with the "
        "second it stopped and the second it restarted. Optional - only mills whose monitor exports stop timings "
        "map it. Feeds the sensor duplicate-timings (sensor-swap) check; a daily-aggregated monitor cannot.",
        (C("stop_date", "DATE", "Shift date the stop belongs to (the sync window key; a night-shift stop after "
           "midnight keeps the shift's date, so it can differ from stop_time's calendar date - use stop_time for that)", True),
         C("loom_code", "TEXT", "Loom the stop is on (the monitor's own loom number / code)", True),
         C("stop_time", "DATETIME", "When the loom stopped (to the second)", True),
         C("restart_time", "DATETIME", "When the loom restarted (to the second)", True),
         C("shift", "TEXT", "Shift, if the monitor records it"),
         C("reason", "TEXT", "Stop reason / code, if the monitor records it")),
        date_column="stop_date",
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
         C("social_security_no", "TEXT", "Social security number, if registered"),
         C("quit", "TEXT", "'Y' if the employee has left the mill (blank/N if still on the books)")),
        key=("emp_code",),
    ),
    Entity(
        "attendance",
        "Last day each employee was marked present. Used to tell who is actually working now, since the "
        "employment status flag is often not kept up to date. One row per employee (latest present day only).",
        (C("emp_code", "TEXT", "Employee code (employees.emp_code)", True),
         C("last_present", "DATE", "Most recent day the employee was marked present")),
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
         C("repaid", "REAL", "Total recovered so far (sum of salary deductions against this loan)"),
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
         C("qty", "REAL", "Quantity on this line, in the line's own unit (uom for store, kg for yarn, meters for fabric)"),
         C("uom", "TEXT", "Unit of qty"),
         C("bags", "REAL", "Yarn gate passes: number of bags"),
         C("cones", "REAL", "Yarn gate passes: loose cones"),
         C("cone_pbag", "REAL", "Yarn gate passes: cones per bag"),
         C("thaan", "REAL", "Fabric gate passes: number of thaans / pieces"),
         C("contract_no", "TEXT", "Contract, when the pass is tied to one"),
         C("beam_no", "TEXT", "Beam number, for beam gate passes"),
         C("entered_by", "TEXT", "ERP user who entered it")),
        date_column="gp_date",
        key=("gatepass_id", "item_code"),
    ),
)

# ERP edit history (item 31). An optional add-on: a mill whose ERP does not record edits can install a small
# history layer (e.g. Oracle triggers writing an AUDIT_HIST schema) that logs every insert / edit / delete of an
# ERP line. Smart Layer reads it read-only and shows it in the Edits tab. A mill without the history tables simply
# has no Edits tab (the entities stay unmapped / "not installed"). Core stays generic; the SQL lives in the mapping.
ENTITIES += (
    Entity(
        "erp_change_events",
        "One row per insert / edit / delete of one ERP line, from the ERP's own change-history layer (optional). "
        "Synced incrementally by event_id, which only ever grows, so the whole history is kept. Optional: only "
        "mills that have installed an edit-history layer map it; its absence means the Edits tab is simply off.",
        (C("event_id", "REAL", "Change event id - a number that only grows; the sync cursor and the join key to "
                               "erp_change_values", True),
         C("changed_at", "DATETIME", "When the change happened (the edit / insert / delete time)", True),
         C("table_name", "TEXT", "The ERP table the changed line belongs to (e.g. TM, TD, DMN, DDT)", True),
         C("action", "TEXT", "I = inserted, U = updated (edited), D = deleted", True),
         C("row_key", "TEXT", "Identifies the exact ERP line, e.g. 'TRANS_NO=123' or 'TRANS_NO=123;SR_NO=2'"),
         C("doc_ref", "TEXT", "Human document reference (voucher type-number, store doc type-number, or 'line n')"),
         C("doc_date", "DATE", "Date on the document the line belongs to"),
         C("erp_user", "TEXT", "ERP login that made the change (taken from the line, or its header for detail lines)"),
         C("pc_name", "TEXT", "PC name the change was made from, if recorded"),
         C("ip_addr", "TEXT", "IP address the change was made from, if recorded"),
         C("db_user", "TEXT", "Database user of the Oracle session"),
         C("os_user", "TEXT", "OS user of the Oracle session"),
         C("client_host", "TEXT", "Client host of the Oracle session"),
         C("client_ip", "TEXT", "Client IP of the Oracle session"),
         C("module", "TEXT", "Oracle session module / program")),
        key=("event_id",),
    ),
    Entity(
        "erp_change_values",
        "One row per field that changed in a change event (optional, pairs with erp_change_events on event_id). "
        "For an edit, only the fields that really changed; for a delete, every old field (new_value blank); for an "
        "insert, no value rows (the values live in the ERP itself). Synced incrementally by event_id.",
        (C("event_id", "REAL", "Change event id (erp_change_events.event_id)", True),
         C("column_name", "TEXT", "The ERP column that changed", True),
         C("old_value", "TEXT", "Value before the change (blank for an insert)"),
         C("new_value", "TEXT", "Value after the change (blank for a delete)")),
    ),
    Entity(
        "erp_doc_headers",
        "Document headers (from the LIVE ERP) for the transactions that appear in the edit history - so a changed "
        "DETAIL line can show its document number, date and who/where it was keyed even when the header row itself "
        "was not edited. Optional, pairs with the edit-history entities. The mapping scopes it to only the TRANS_NOs "
        "present in erp_change_events (a join on the row_key), so it stays small: one row per edited document.",
        (C("table_name", "TEXT", "The header ERP table (e.g. TM for vouchers, DMN for store documents)", True),
         C("trans_no", "TEXT", "The header's TRANS_NO (matches the TRANS_NO inside a line's row_key)", True),
         C("doc_ref", "TEXT", "Human document reference (voucher type-number, store doc type-number)"),
         C("doc_date", "DATE", "Date on the document"),
         C("erp_user", "TEXT", "ERP login that keyed the document"),
         C("pc_name", "TEXT", "PC the document was keyed from")),
        key=("table_name", "trans_no"),
    ),
)

# Purchasing (item 30b), loom standards (30c), yarn purchase contracts (30d). All OPTIONAL add-ons: a mill that
# does not map them simply has no Purchasing tab / no loom-standard column / no yarn-contracts list. Generic core.
ENTITIES += (
    Entity(
        "purchase_indents",
        "Store purchase indents (demand raised by a department), one row per indent line. Optional.",
        (C("indent_no", "TEXT", "Indent number", True),
         C("indent_date", "DATE", "Indent date", True),
         C("trans_no", "TEXT", "The ERP transaction id (links a PO line back to its indent)"),
         C("sr_no", "TEXT", "Line number within the indent"),
         C("uc", "TEXT", "Unit/company code (part of the ERP key)"),
         C("dept", "TEXT", "Department that raised it"),
         C("item_code", "TEXT", "Item code (items.item_code)", True),
         C("qty", "REAL", "Quantity indented"),
         C("required_date", "DATE", "Date the item is needed"),
         C("purpose", "TEXT", "Purpose / comments"),
         C("demand_by", "TEXT", "Who demanded it"),
         C("approved", "TEXT", "Approval status")),
        date_column="indent_date",
    ),
    Entity(
        "purchase_orders",
        "Purchase orders, one row per PO line, with the matching goods-receipt (GRN) figures where the line has been "
        "received - so PO rate/qty can be compared against what was actually received, with the supplier's terms. "
        "received_qty blank = still pending. Optional.",
        (C("po_no", "TEXT", "PO number", True),
         C("po_date", "DATE", "PO date", True),
         C("trans_no", "TEXT", "The ERP transaction id of the PO"),
         C("sr_no", "TEXT", "Line number within the PO"),
         C("uc", "TEXT", "Unit/company code (part of the ERP key)"),
         C("supplier_code", "TEXT", "Supplier account code"),
         C("supplier_name", "TEXT", "Supplier name"),
         C("item_code", "TEXT", "Item code (items.item_code)", True),
         C("rate", "REAL", "PO rate per unit"),
         C("qty", "REAL", "PO quantity"),
         C("purchaser", "TEXT", "Purchaser / buyer"),
         C("payment_term", "TEXT", "Payment term text"),
         C("paid", "TEXT", "Paid / unpaid status"),
         C("payment_date", "DATE", "Payment date, if paid"),
         C("ref_indent_no", "TEXT", "Indent number this PO line fulfils (links to purchase_indents)"),
         C("ref_indent_trans", "TEXT", "Indent transaction id this PO line references"),
         C("ref_indent_sr", "TEXT", "Indent line number this PO line references"),
         C("received_qty", "REAL", "Quantity actually received against this PO line (blank = not received yet)"),
         C("received_rate", "REAL", "Rate on the goods receipt (to compare with the PO rate)"),
         C("received_date", "DATE", "Date the goods were received"),
         C("grn_no", "TEXT", "Goods-receipt (GRN) number, when received")),
        date_column="po_date",
    ),
    Entity(
        "loom_standards",
        "Per-loom standard settings by period (from the ERP's loom-detail table): the standard RPM, and the ERP's "
        "production / target figures where it keeps them (often empty). Optional - used to show efficiency against a "
        "standard. A 'target' is only shown when the ERP or config provides one; a bare RPM is labelled as RPM.",
        (C("loom_code", "TEXT", "Loom code (looms.loom_code)", True),
         C("from_date", "DATE", "Period start"),
         C("to_date", "DATE", "Period end"),
         C("standard_rpm", "REAL", "Standard / booked RPM for the loom in this period"),
         C("production", "REAL", "ERP-kept production figure, if any"),
         C("target", "REAL", "ERP-kept target figure, if any")),
        key=("loom_code", "from_date"),
    ),
    Entity(
        "yarn_contracts",
        "Yarn purchase/sale contracts (count, brand, quantity, rate, delivery) - the buying-side contracts, distinct "
        "from weaving contracts. Optional; a mill that does not map it has no yarn-contracts list.",
        (C("contract_no", "TEXT", "Contract number", True),
         C("contract_date", "DATE", "Contract date"),
         C("contract_type", "TEXT", "Type (e.g. P = purchase)"),
         C("buyer", "TEXT", "Buyer account/name"),
         C("seller", "TEXT", "Seller account/name"),
         C("yarn_code", "TEXT", "Yarn code"),
         C("yarn_name", "TEXT", "Yarn / count description"),
         C("brand", "TEXT", "Brand"),
         C("bags", "REAL", "Quantity (bags)"),
         C("rate", "REAL", "Rate"),
         C("delivery_date", "DATE", "Delivery / last delivery date"),
         C("payment_term", "TEXT", "Payment term"),
         C("status", "TEXT", "Status (e.g. RUNNING)")),
        date_column="contract_date",
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
