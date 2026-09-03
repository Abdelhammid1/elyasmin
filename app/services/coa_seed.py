"""ACCOUNTING FOUNDATION — the default farm chart of accounts.

Idempotent: running seed_default_coa() a second time is a no-op. Every
existing treasury Account row is wired to its matching COA leaf so payments
route to the right sub-account.

Codes are numeric strings, hierarchical by prefix (1100 is a child of 1).
Every leaf marked `postable=True` accepts journal lines; headers (postable
missing or False) exist only for grouping.
"""
from typing import Optional

from app.extensions import db
from app.models.accounting import (
    Account, AccountType, NormalSide, NORMAL_SIDE_FOR_TYPE,
)


# (code, name_ar, name_en, type, is_postable, parent_code)
DEFAULT_COA = [
    # ---- ASSETS ----
    ("1",    "الأصول",                    "Assets",              AccountType.ASSET,     False, None),
    ("1100", "الأصول المتداولة",           "Current Assets",      AccountType.ASSET,     False, "1"),
    ("1110", "خزينة نقدية",                "Cash on Hand",        AccountType.ASSET,     True,  "1100"),
    ("1120", "حسابات بنكية",              "Bank Accounts",       AccountType.ASSET,     True,  "1100"),
    ("1200", "مخزون المواد الخام",         "Raw Materials",       AccountType.ASSET,     True,  "1100"),
    ("1210", "مخزون العلف",                "Feed Inventory",      AccountType.ASSET,     True,  "1100"),
    ("1220", "مخزون الأدوية",              "Medicine Inventory",  AccountType.ASSET,     True,  "1100"),
    ("1300", "ذمم العملاء",                "Trade Receivables",   AccountType.ASSET,     True,  "1100"),
    ("1400", "حيوانات المزرعة",            "Livestock",           AccountType.ASSET,     True,  "1100"),
    ("1500", "الأصول الثابتة",             "Fixed Assets",        AccountType.ASSET,     False, "1"),
    ("1510", "معدات وآلات",                "Equipment",           AccountType.ASSET,     True,  "1500"),

    # ---- LIABILITIES ----
    ("2",    "الخصوم",                     "Liabilities",         AccountType.LIABILITY, False, None),
    ("2100", "ذمم الموردين",                "Trade Payables",      AccountType.LIABILITY, True,  "2"),
    ("2200", "رواتب مستحقة",                "Wages Payable",       AccountType.LIABILITY, True,  "2"),
    ("2900", "خصوم أخرى",                   "Other Liabilities",   AccountType.LIABILITY, True,  "2"),

    # ---- EQUITY ----
    ("3",    "حقوق الملكية",                "Equity",              AccountType.EQUITY,    False, None),
    ("3100", "رأس المال",                   "Owner's Capital",     AccountType.EQUITY,    True,  "3"),
    ("3200", "الأرباح المحتجزة",            "Retained Earnings",   AccountType.EQUITY,    True,  "3"),
    ("3900", "أرصدة افتتاحية",              "Opening Balances",    AccountType.EQUITY,    True,  "3"),

    # ---- REVENUE ----
    ("4",    "الإيرادات",                   "Revenue",             AccountType.REVENUE,   False, None),
    ("4100", "إيرادات اللبن",               "Milk Revenue",        AccountType.REVENUE,   True,  "4"),
    ("4200", "إيرادات بيع الحيوانات",       "Livestock Sales",     AccountType.REVENUE,   True,  "4"),
    ("4900", "إيرادات أخرى",                "Other Revenue",       AccountType.REVENUE,   True,  "4"),

    # ---- EXPENSES ----
    ("5",    "المصروفات",                   "Expenses",            AccountType.EXPENSE,   False, None),
    ("5100", "تكلفة الأعلاف",               "Feed Cost",           AccountType.EXPENSE,   True,  "5"),
    ("5200", "أجور العمالة",                "Labour Wages",        AccountType.EXPENSE,   True,  "5"),
    ("5300", "كهرباء ومياه",                "Utilities",           AccountType.EXPENSE,   True,  "5"),
    ("5310", "صيانة",                       "Maintenance",         AccountType.EXPENSE,   True,  "5"),
    ("5320", "إيجار",                       "Rent",                AccountType.EXPENSE,   True,  "5"),
    ("5400", "أدوية بيطرية",                "Veterinary",          AccountType.EXPENSE,   True,  "5"),
    ("5500", "نقل ومصاريف تشغيلية",         "Transport & Ops",     AccountType.EXPENSE,   True,  "5"),
    ("5900", "مصروفات متنوعة",              "Miscellaneous",       AccountType.EXPENSE,   True,  "5"),
]


def seed_default_coa() -> dict:
    """Create every account in DEFAULT_COA that does not already exist. Never
    updates or renames — safe to re-run on any DB. Returns a `{code: Account}`
    dict for every account in the chart, seeded or pre-existing."""
    existing = {a.code: a for a in Account.query.all()}
    for code, name_ar, name_en, atype, postable, parent_code in DEFAULT_COA:
        if code in existing:
            continue
        parent = existing.get(parent_code) if parent_code else None
        acc = Account(
            code=code,
            name=name_ar,
            name_en=name_en,
            type=atype,
            normal_side=NORMAL_SIDE_FOR_TYPE[atype],
            parent_id=parent.id if parent else None,
            is_postable=postable,
            is_active=True,
        )
        db.session.add(acc)
        db.session.flush()   # need id for downstream parents
        existing[code] = acc
    return existing


def wire_treasury_accounts(coa: Optional[dict] = None) -> int:
    """Attach every treasury row (app.models.finance.Account) to a COA leaf.

    Cash accounts fall under 1110, bank accounts fall under 1120. If a treasury
    row has no COA link yet, create a per-account leaf named after it and hang
    it under the right parent. Returns the number of new leaves created."""
    from app.models.finance import Account as TreasuryAccount

    if coa is None:
        coa = {a.code: a for a in Account.query.all()}

    parent_by_kind = {"cash": coa.get("1110"), "bank": coa.get("1120")}
    created = 0

    for treasury in TreasuryAccount.query.filter_by(is_archived=False).all():
        already = (
            Account.query
            .filter_by(treasury_account_id=treasury.id)
            .first()
        )
        if already:
            continue

        parent = parent_by_kind.get(treasury.account_type)
        if parent is None:
            continue  # unrecognised treasury kind — leave for a later pass

        # Code = parent_code + serial suffix so codes stay unique.
        base = parent.code
        siblings = Account.query.filter(Account.parent_id == parent.id).count()
        code = f"{base}-{siblings + 1:02d}"

        leaf = Account(
            code=code,
            name=treasury.display_name,
            type=AccountType.ASSET,
            normal_side=NormalSide.DEBIT,
            parent_id=parent.id,
            is_postable=True,
            is_active=True,
            treasury_account_id=treasury.id,
        )
        db.session.add(leaf)
        db.session.flush()
        created += 1
    return created
