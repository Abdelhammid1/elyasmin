"""PHASE 6 (2/3) — medicine lots + FIFO dispense + lot_id on movements

Every medicine ingredient with existing stock gets one legacy lot so the
FIFO picker has something to draw from immediately. `stock_movements`
and `medicine_dispenses` grow a nullable `lot_id` — nullable because
feed and pre-migration medicine rows have no lot.

Revision ID: a9771d70c528
Revises: 6c0d074ad3f1
Create Date: 2026-09-03
"""
import sqlalchemy as sa
from alembic import op

revision = "a9771d70c528"
down_revision = "6c0d074ad3f1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "medicine_lots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ingredient_id", sa.Integer,
                  sa.ForeignKey("ingredients.id"), nullable=False, index=True),
        sa.Column("lot_number", sa.String(60), nullable=True),
        sa.Column("expires_on", sa.Date, nullable=True, index=True),
        sa.Column("qty_received", sa.Numeric(14, 3), nullable=False),
        sa.Column("qty_remaining", sa.Numeric(14, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("source_type", sa.String(40), nullable=False,
                  server_default=sa.text("'PurchaseInvoice'")),
        sa.Column("source_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False,
                  server_default=sa.func.now()),
        sa.Column("created_by_id", sa.Integer,
                  sa.ForeignKey("users.id"), nullable=True),
        sa.UniqueConstraint(
            "ingredient_id", "source_type", "source_id", "lot_number",
            name="uq_med_lot_source",
        ),
    )

    with op.batch_alter_table("stock_movements") as batch:
        batch.add_column(sa.Column(
            "lot_id", sa.Integer,
            sa.ForeignKey("medicine_lots.id", name="fk_stockmove_lot"),
            nullable=True,
        ))
        batch.create_index("ix_stock_movements_lot_id", ["lot_id"])

    with op.batch_alter_table("medicine_dispenses") as batch:
        batch.add_column(sa.Column(
            "lot_id", sa.Integer,
            sa.ForeignKey("medicine_lots.id", name="fk_dispense_lot"),
            nullable=True,
        ))
        batch.create_index("ix_medicine_dispenses_lot_id", ["lot_id"])

    # Backfill: one legacy lot per medicine ingredient that already has
    # stock. Old rows have no lot_number and no expiry — the FIFO picker
    # sorts NULL-expiry last, so a legacy lot is drawn from only after
    # every dated lot is empty (which is what you want).
    op.execute("""
        INSERT INTO medicine_lots
            (ingredient_id, lot_number, expires_on, qty_received,
             qty_remaining, unit_cost, source_type, source_id, created_at)
        SELECT id, NULL, NULL, current_qty, current_qty, avg_cost,
               'OpeningInventory', id, CURRENT_TIMESTAMP
        FROM ingredients
        WHERE category = 'medicine' AND current_qty > 0
    """)


def downgrade():
    with op.batch_alter_table("medicine_dispenses") as batch:
        batch.drop_index("ix_medicine_dispenses_lot_id")
        batch.drop_column("lot_id")
    with op.batch_alter_table("stock_movements") as batch:
        batch.drop_index("ix_stock_movements_lot_id")
        batch.drop_column("lot_id")
    op.drop_table("medicine_lots")
