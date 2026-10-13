"""
A mill's own closing method, read from config [closing]. Nothing is fixed in code:
every mill sets its divisor, bag size, closing counts, rejection rule, basis,
allowances, and may replace the yarn-weight formula entirely.

Settings can be overridden per party ([[closing.parties]], matched by name or
code) and per contract ([[closing.contracts]]); per-yarn closing counts and
rates go in [closing.yarns."<yarn code>"].

The weight formula is a plain arithmetic expression giving POUNDS of yarn per
METER of fabric, with these names available:
    threads_per_inch   ends per inch (warp) or picks per inch (weft)
    width_in           fabric width in inches
    count              closing count of the yarn
    divisor            the mill's divisor (840 = hank length; many mills use 800)
    crimp_pct          take-up allowance for this side (warp or weft), in percent
Allowed: numbers, those names, + - * / ** ( ), min(), max(), round().
"""

import ast
import operator
import re

KG_PER_LB = 0.45359237

DEFAULT_CLOSING = {
    "weight_formula": "threads_per_inch * width_in * 1.0936 / divisor / count * (1 + crimp_pct / 100)",
    "divisor": 840,
    "bag_lb": 100,
    "count_margin": 0,               # closing count = yarn count - margin, unless the yarn has its own closing count
    "basis": "dispatched",           # dispatched (net of returns) | woven
    "rejection_charged_to": "mill",  # mill: party is charged yarn for fresh meters only | party: for all meters
    "wastage_allowance_pct": 0,      # extra yarn allowed to the mill on top of the calculated consumption
    "erp_start_date": "",            # this ERP's go-live date (YYYY-MM-DD); contracts that started earlier are
                                     # incomplete here (their yarn / fabric history is in the previous ERP)
    "warp_crimp_pct": 0,
    "weft_crimp_pct": 0,
    "yarns": {},                     # {"Y40": {closing_count = 39, rate_per_lb = 550}}
    "yarn_groups": [],               # [["6", "18"]]: one yarn booked under several codes (first code names the group)
    "parties": [],                   # [{match = "WAHAB", divisor = 800, ...}]
    "contracts": [],                 # [{contract_no = "46", picks_per_inch = 64, ...}]
}
OVERRIDABLE = ("weight_formula", "divisor", "bag_lb", "count_margin", "basis", "rejection_charged_to",
               "wastage_allowance_pct", "warp_crimp_pct", "weft_crimp_pct")

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"min": min, "max": max, "round": round}


class FormulaError(ValueError):
    pass


def compile_formula(text):
    """Parse once; returns a function(**names) -> float. Only arithmetic is allowed."""
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"weight_formula is not valid: {e.msg}") from None

    def ev(node, names):
        if isinstance(node, ast.Expression):
            return ev(node.body, names)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise FormulaError(f"unknown name '{node.id}' in weight_formula (allowed: {', '.join(sorted(names))})")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left, names), ev(node.right, names))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand, names))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
                and not node.keywords):
            return _FUNCS[node.func.id](*(ev(a, names) for a in node.args))
        raise FormulaError("weight_formula may only use numbers, names, + - * / ** ( ) and min/max/round")

    return lambda **names: float(ev(tree, names))


class Method:
    def __init__(self, closing_cfg):
        self.cfg = {**DEFAULT_CLOSING, **(closing_cfg or {})}
        self._formulas = {}
        self.weight("warp", self.cfg, 80, 60, 40)          # fail early on a bad formula

    def settings(self, contract_no=None, party=None):
        """Mill defaults, then the matching party override, then the contract override."""
        s = {k: self.cfg[k] for k in OVERRIDABLE}
        yarns = {}                                  # per-party / per-contract yarn counts and rates (same keys as [closing.yarns])
        for p in self.cfg["parties"]:
            if party and re.search(str(p.get("match", "^$")), party, re.I):
                s.update({k: v for k, v in p.items() if k in OVERRIDABLE})
                yarns.update({str(k): v for k, v in p.get("yarns", {}).items()})
        for c in self.cfg["contracts"]:
            if contract_no is not None and str(c.get("contract_no")) == str(contract_no):
                s.update({k: v for k, v in c.items() if k in OVERRIDABLE})
                yarns.update({str(k): v for k, v in c.get("yarns", {}).items()})
        s["_yarns"] = yarns
        return s

    def contract_overrides(self, contract_no):
        """Construction corrections for one contract (e.g. picks the ERP has wrong)."""
        for c in self.cfg["contracts"]:
            if str(c.get("contract_no")) == str(contract_no):
                return {k: v for k, v in c.items() if k not in OVERRIDABLE and k not in ("contract_no", "yarns")}
        return {}

    def group(self, code):
        """The yarn account a code belongs to (codes in one yarn_groups entry share one balance)."""
        for g in self.cfg["yarn_groups"]:
            if str(code) in [str(c) for c in g]:
                return str(g[0])
        return code

    def yarn(self, code):
        return self.cfg["yarns"].get(str(code), {}) if code is not None else {}

    def closing_count(self, side, yarn_code, count, s):
        y = {**self.yarn(yarn_code), **s.get("_yarns", {}).get(str(yarn_code), {})}
        own = y.get(f"{side}_closing_count", y.get("closing_count"))
        if own:
            return float(own)
        return float(count) - float(s["count_margin"]) if count else None

    def weight(self, side, s, threads, width, count):
        """lb of yarn per meter of fabric for one side (warp/weft); None when data is missing."""
        if not (threads and width and count):
            return None
        f = self._formulas.get(s["weight_formula"])
        if f is None:
            f = self._formulas[s["weight_formula"]] = compile_formula(s["weight_formula"])
        return f(threads_per_inch=float(threads), width_in=float(width), count=float(count),
                 divisor=float(s["divisor"]), crimp_pct=float(s[f"{side}_crimp_pct"]))

    def rate(self, yarn_code, s=None):
        r = {**self.yarn(yarn_code), **((s or {}).get("_yarns", {}).get(str(yarn_code), {}))}.get("rate_per_lb")
        return float(r) if r else None
