"""ACCOUNTING FOUNDATION — the default farm chart of accounts.

Idempotent: running seed_default_coa() a second time is a no-op. Every
existing TreasuryAccount row is wired to its matching COA leaf so payments
route to the right sub-account.

Codes are numeric strings, hierarchical by prefix (1100 is a child of 1).
Every leaf marked `postable=True` accepts journal lines; headers (postable
missing or False) exist only for grouping.
"""
from typing import Optional

from app.extensions import db
from app.models.accounting import (
    LedgerAccount, AccountType, NormalSide, NORMAL_SIDE_FOR_TYPE,
)


# PHASE 8c — codes match Ibrahim's spec throughout.
# (code, name_ar, name_en, type, is_postable, parent_code)
DEFAULT_COA = [
    # ---- ASSETS ----
    ("1",    "الأصول",                    "Assets",              AccountType.ASSET,     False, None),
    ("1000", "الأصول المتداولة",           "Current Assets",      AccountType.ASSET,     False, "1"),
    ("1010", "خزينة نقدية",                "Cash on Hand",        AccountType.ASSET,     True,  "1000"),
    ("1020", "حسابات بنكية",              "Bank Accounts",       AccountType.ASSET,     True,  "1000"),
    ("1030", "شيكات تحت التحصيل",           "Checks Receivable",   AccountType.ASSET,     True,  "1000"),
    ("1100", "ذمم العملاء",                "Trade Receivables",   AccountType.ASSET,     True,  "1000"),
    ("1200", "مخزون المواد الخام",         "Raw Materials",       AccountType.ASSET,     True,  "1000"),
    ("1210", "مخزون العلف",                "Feed Inventory",      AccountType.ASSET,     True,  "1000"),
    ("1220", "مخزون الأدوية",              "Medicine Inventory",  AccountType.ASSET,     True,  "1000"),
    ("1400", "حيوانات المزرعة",            "Livestock",           AccountType.ASSET,     True,  "1000"),
    ("1300", "الأصول الثابتة",             "Fixed Assets",        AccountType.ASSET,     False, "1"),
    ("1310", "معدات وآلات",                "Equipment",           AccountType.ASSET,     True,  "1300"),
    # 1320 is a contra-asset (accumulated depreciation) — sits under
    # 1300 fixed assets. Display reads sum(DR - CR) so no explicit
    # normal_side flip is needed at the storage layer.
    ("1320", "مجمع إهلاك المعدات",           "Accum. Depreciation", AccountType.ASSET,     True,  "1300"),

    # ---- LIABILITIES ----
    ("2",    "الخصوم",                     "Liabilities",         AccountType.LIABILITY, False, None),
    ("2010", "ذمم الموردين",                "Trade Payables",      AccountType.LIABILITY, True,  "2"),
    ("2020", "شيكات تحت الدفع",              "Checks Payable",      AccountType.LIABILITY, True,  "2"),
    ("2030", "رواتب مستحقة",                "Wages Payable",       AccountType.LIABILITY, True,  "2"),
    ("2040", "قروض",                        "Loans",               AccountType.LIABILITY, True,  "2"),
    ("2090", "خصوم أخرى",                   "Other Liabilities",   AccountType.LIABILITY, True,  "2"),

    # ---- EQUITY ----
    ("3",    "حقوق الملكية",                "Equity",              AccountType.EQUITY,    False, None),
    ("3010", "رأس المال",                   "Owner's Capital",     AccountType.EQUITY,    True,  "3"),
    ("3020", "الأرباح المحتجزة",            "Retained Earnings",   AccountType.EQUITY,    True,  "3"),
    ("3030", "مسحوبات صاحب العمل",           "Owner Draws",         AccountType.EQUITY,    True,  "3"),
    ("3090", "أرصدة افتتاحية",              "Opening Balances",    AccountType.EQUITY,    True,  "3"),

    # ---- REVENUE ----
    ("4",    "الإيرادات",                   "Revenue",             AccountType.REVENUE,   False, None),
    ("4010", "إيرادات اللبن",               "Milk Revenue",        AccountType.REVENUE,   True,  "4"),
    ("4020", "إيرادات بيع الحيوانات",       "Livestock Sales",     AccountType.REVENUE,   True,  "4"),
    ("4090", "إيرادات أخرى",                "Other Revenue",       AccountType.REVENUE,   True,  "4"),

    # ---- EXPENSES ----
    ("5",    "المصروفات",                   "Expenses",            AccountType.EXPENSE,   False, None),
    ("5010", "تكلفة الأعلاف",               "Feed Cost",           AccountType.EXPENSE,   True,  "5"),
    ("5020", "أدوية بيطرية",                "Veterinary",          AccountType.EXPENSE,   True,  "5"),
    ("5030", "أجور العمالة",                "Labour Wages",        AccountType.EXPENSE,   True,  "5"),
    ("5040", "كهرباء ومياه",                "Utilities",           AccountType.EXPENSE,   True,  "5"),
    ("5050", "صيانة",                       "Maintenance",         AccountType.EXPENSE,   True,  "5"),
    ("5060", "إيجار",                       "Rent",                AccountType.EXPENSE,   True,  "5"),
    ("5070", "مصروف الإهلاك",                "Depreciation Expense",AccountType.EXPENSE,   True,  "5"),
    ("5075", "نقل ومصاريف تشغيلية",         "Transport & Ops",     AccountType.EXPENSE,   True,  "5"),
    ("5080", "مصروفات متنوعة",              "Miscellaneous",       AccountType.EXPENSE,   True,  "5"),
]


def seed_default_coa() -> dict:
    """Create every account in DEFAULT_COA that does not already exist. Never
    updates or renames — safe to re-run on any DB. Returns a `{code: LedgerAccount}`
    dict for every account in the chart, seeded or pre-existing."""
    existing = {a.code: a for a in LedgerAccount.query.all()}
    for code, name_ar, name_en, atype, postable, parent_code in DEFAULT_COA:
        if code in existing:
            continue
        parent = existing.get(parent_code) if parent_code else None
        acc = LedgerAccount(
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


def ensure_check_accounts() -> None:
    """PHASE 8a — called from the checks migration to seed 1130/2110 the
    first time the DB is migrated onto phase 8, without needing to
    re-run the entire seed. `seed_default_coa` would work too but is
    heavier."""
    seed_default_coa()
    db.session.commit()


def wire_treasury_accounts(coa: Optional[dict] = None) -> int:
    """Attach every treasury row (app.models.finance.TreasuryAccount) to a COA leaf.

    Cash accounts fall under 1010, bank accounts fall under 1020. If a treasury
    row has no COA link yet, create a per-account leaf named after it and hang
    it under the right parent. Returns the number of new leaves created."""
    from app.models.finance import TreasuryAccount

    if coa is None:
        coa = {a.code: a for a in LedgerAccount.query.all()}

    parent_by_kind = {"cash": coa.get("1010"), "bank": coa.get("1020")}
    created = 0

    for treasury in TreasuryAccount.query.filter_by(is_archived=False).all():
        already = (
            LedgerAccount.query
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
        siblings = LedgerAccount.query.filter(LedgerAccount.parent_id == parent.id).count()
        code = f"{base}-{siblings + 1:02d}"

        leaf = LedgerAccount(
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
