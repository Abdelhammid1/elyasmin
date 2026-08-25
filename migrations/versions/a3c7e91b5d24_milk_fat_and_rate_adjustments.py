"""TICKET-A: fat % in the analysis, and التعديلات become rates per kilo

Revision ID: a3c7e91b5d24
Revises: f2b9d4c17e60
Create Date: 2026-08-25

The adjustment columns used to hold flat EGP amounts. The client enters them as
rates per kilo, so every value he ever typed was applied without being multiplied
by the delivery quantity.

Converting them means dividing each stored amount by that delivery's qty_kg. The
columns are Numeric(14,2), and 0.56 EGP over 24,450 kg is a rate of 0.0000229…,
which rounds to 0.00 at two decimal places — so the columns MUST be widened
before anything is divided, or the conversion destroys the data it is migrating.
That ordering is the whole point of this migration.

Totals are preserved: after conversion, rate × qty reproduces the original
amount, so no historical invoice changes value. The migration asserts this and
fails rather than silently repricing the client's history.
"""
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from alembic import op

revision = "a3c7e91b5d24"
down_revision = "f2b9d4c17e60"
branch_labels = None
depends_on = None

ADJ_COLS = ("fat_bonus", "protein_bonus", "bacteria_adj", "transport", "other_adj")
CENT = Decimal("0.01")


def upgrade():
    # 1) fat % joins the analysis
    with op.batch_alter_table("milk_deliveries") as b:
        b.add_column(sa.Column("fat_pct", sa.Numeric(5, 2), nullable=True))

    # 2) widen BEFORE converting — a per-kilo rate does not fit in 2 decimals
    with op.batch_alter_table("milk_deliveries") as b:
        for col in ADJ_COLS:
            b.alter_column(
                col,
                existing_type=sa.Numeric(14, 2),
                type_=sa.Numeric(18, 10),
                existing_nullable=False,
            )

    # 3) amounts -> rates, keeping every total identical
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, qty_kg, subtotal, total_value, "
            + ", ".join(ADJ_COLS)
            + " FROM milk_deliveries"
        )
    ).mappings().all()

    skipped = []
    for r in rows:
        try:
            qty = Decimal(str(r["qty_kg"] or 0))
        except InvalidOperation:
            qty = Decimal("0")

        amounts = {c: Decimal(str(r[c] or 0)) for c in ADJ_COLS}
        if all(a == 0 for a in amounts.values()):
            continue  # nothing to convert, and no division to risk

        if qty <= 0:
            # An amount cannot be expressed as a rate without a quantity, and a
            # zero-quantity delivery contributes nothing once rates are multiplied
            # out. Leaving the old amount sitting in a column that now means
            # "per kilo" would show a nonsense rate on the form, so clear it and
            # name the row rather than fail the whole upgrade over a degenerate
            # record.
            conn.execute(
                sa.text(
                    "UPDATE milk_deliveries SET "
                    + ", ".join(f"{c} = 0" for c in ADJ_COLS)
                    + " WHERE id = :id"
                ),
                {"id": r["id"]},
            )
            skipped.append(r["id"])
            continue

        rates = {c: (a / qty) for c, a in amounts.items()}
        conn.execute(
            sa.text(
                "UPDATE milk_deliveries SET "
                + ", ".join(f"{c} = :{c}" for c in ADJ_COLS)
                + " WHERE id = :id"
            ),
            {**{c: str(v) for c, v in rates.items()}, "id": r["id"]},
        )

        # the promise of this migration: the invoice still says the same thing
        rebuilt = (sum(rates.values()) * qty).quantize(CENT)
        original = sum(amounts.values()).quantize(CENT)
        if rebuilt != original:
            raise RuntimeError(
                f"delivery {r['id']}: converting adjustments changed the invoice "
                f"({original} -> {rebuilt}). Refusing to reprice the client's history."
            )

    if skipped:
        print(
            f"  [warn] {len(skipped)} delivery(ies) had adjustments but no quantity; "
            f"their adjustment boxes were left at 0: {skipped}"
        )

    # 4) the new coefficients, shipped inert so no price moves on deploy
    for key, value, label in (
        ("quality_fat_ref", "3.0", "نسبة الدهن اللي الزيادة بتبدأ فوقها"),
        ("quality_fat_adj", "0", "زيادة السعر لكل +1% دهن"),
    ):
        exists = conn.execute(
            sa.text("SELECT 1 FROM settings WHERE key = :k"), {"k": key}
        ).first()
        if not exists:
            conn.execute(
                sa.text(
                    "INSERT INTO settings (key, value, description) "
                    "VALUES (:k, :v, :d)"
                ),
                {"k": key, "v": value, "d": label},
            )


def downgrade():
    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, qty_kg, " + ", ".join(ADJ_COLS) + " FROM milk_deliveries")
    ).mappings().all()

    for r in rows:
        qty = Decimal(str(r["qty_kg"] or 0))
        rates = {c: Decimal(str(r[c] or 0)) for c in ADJ_COLS}
        if all(v == 0 for v in rates.values()):
            continue
        amounts = {c: (v * qty).quantize(CENT) for c, v in rates.items()}
        conn.execute(
            sa.text(
                "UPDATE milk_deliveries SET "
                + ", ".join(f"{c} = :{c}" for c in ADJ_COLS)
                + " WHERE id = :id"
            ),
            {**{c: str(v) for c, v in amounts.items()}, "id": r["id"]},
        )

    with op.batch_alter_table("milk_deliveries") as b:
        for col in ADJ_COLS:
            b.alter_column(
                col,
                existing_type=sa.Numeric(18, 10),
                type_=sa.Numeric(14, 2),
                existing_nullable=False,
            )
        b.drop_column("fat_pct")

    conn.execute(
        sa.text("DELETE FROM settings WHERE key IN ('quality_fat_ref', 'quality_fat_adj')")
    )
