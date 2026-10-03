"""
old_erp_account_map.py
Maps the old ERP's chart of accounts (schema DOSTT, Jan-2014 .. 30-Sep-2023,
5-char heads) onto the new ERP's level-3 heads (DOSTEX, 6-char codes) so the
two ledgers can be reported as one history.

Built 2026-09-25 by comparing DOSTT closing balances at 30-Sep-2023 with the
DOSTEX opening balances (ABALS) the new ERP was started with - see
43_merged_financials.py's reconciliation sheet. Leaf-level names are matched
separately (same AN in both charts) where a head splits in the new chart,
e.g. old 70130 MISC. INCOME -> new 350201 waste / 350202 bardana / 350203 scrap.
"""

HEAD_MAP = {
    # equity
    "11110": "100101", "15120": "100101", "15110": "150202",
    "12110": "100202", "15125": "100303", "15135": "100303", "15160": "100303",
    "15170": "100301", "45110": "100201", "45180": "100201",
    # creditors / accrued
    "30115": "200101", "30120": "200105", "30130": "200106", "30140": "200109",
    "30145": "200109", "30160": "200109",
    "35110": "200203", "35115": "200203", "35120": "200203", "35130": "200203",
    "35140": "200203", "35141": "200203", "35142": "200203", "35170": "200203",
    "35180": "200203", "35150": "200202", "35190": "200202", "35160": "200201",
    # fixed assets
    "40110": "250101", "40120": "250101", "40130": "250102", "40140": "250106",
    "40150": "250107", "40160": "250105", "40165": "250105", "40170": "250104",
    "40180": "250103", "40185": "250103", "40190": "250103",
    "42120": "250201", "42130": "250202",
    # current assets
    "50110": "300306", "50140": "300306",
    **{f"551{i:02d}": "300401" for i in range(1, 14)},
    "55145": "300402", "55150": "300402", "55185": "300601", "55190": "300601",
    "55180": "300601", "55160": "300502", "55170": "300501",
    "60110": "300101", "60130": "300201",
    "65110": "300801", "65120": "300802", "65130": "300802",
    # revenue
    "70110": "350104", "70115": "350104", "70120": "350106", "70125": "350105",
    "70130": "350201", "70150": "350104", "70160": "350105", "70170": "350201",
    "70180": "350205", "46110": "350205", "46130": "350205",
    # cost of production
    "75105": "400101", "75110": "400201", "75115": "400301", "75120": "400401",
    "75125": "400601", "75130": "400701", "75140": "400702", "75145": "400501",
    "75150": "400705", "75155": "400704", "75160": "400703", "75175": "400706",
    "75180": "400707", "75190": "400801",
    # admin & selling
    "85105": "450101", "85110": "450201", "85115": "450202", "85120": "450203",
    "85124": "450204", "85125": "450205", "85126": "450205", "85130": "450207",
    "85140": "450208", "85145": "450209", "85150": "450210", "85160": "450211",
    "85170": "450215", "85180": "450216", "85185": "450217", "85190": "400801",
    # financial
    "95110": "550101", "95120": "550102",
}

# Leaf accounts that land in a different new head than their old head's
# default (the new chart split these heads). Matched by account name.
LEAF_OVERRIDES = {
    "701300001": "350202",  # SALE OF BARDANA
    "701300004": "350203",  # SALE OF SCRAPE
    "751100002": "400202",  # PESSI EXP
    "751100003": "400202",  # EOBI EXP
    "751100004": "400202",  # EDUCATION CESS EXP
    "751200002": "400402",  # GENERATOR EXPENSES -> FUELS
    "851250004": "450206",  # POSTAGE CHGS
    "851600001": "450213",  # PROFESSIONAL TAX
    "851600002": "450212",  # PROPERTY TAX
    "551700003": "300501",  # SALES TAX RECEIVEABLE
    "551700004": "300501",  # INCOME TAX R/A
}


def new_head(old_ac):
    """New-chart level-3 head (6 chars) for an old-chart leaf account."""
    if not old_ac:
        return None
    return LEAF_OVERRIDES.get(old_ac) or HEAD_MAP.get(old_ac[:5])
