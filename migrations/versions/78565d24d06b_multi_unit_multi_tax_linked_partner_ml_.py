"""multi-unit + multi-tax + linked partner + ml unit

Revision ID: 78565d24d06b
Revises: 93016b312d3c
Create Date: 2026-08-02 10:17:27.308020

"""
from alembic import op
import sqlalchemy as sa


revision = '78565d24d06b'
down_revision = '93016b312d3c'
branch_labels = None
depends_on = None


def upgrade():
    # -----------------------------------------------------------------
    # 1) TICKET-2: new IngredientUnit table (empty; seeded below via data migration)
    # -----------------------------------------------------------------
    op.create_table(
        'ingredient_units',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ingredient_id', sa.Integer(), nullable=False),
        sa.Column('unit_code', sa.String(length=40), nullable=False),
        sa.Column('unit_label', sa.String(length=60), nullable=False),
        sa.Column('factor_to_base', sa.Numeric(precision=14, scale=6), nullable=False),
        sa.Column('is_default_purchase', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(['ingredient_id'], ['ingredients.id'], name='fk_ingunit_ingredient'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ingredient_id', 'unit_code', name='uq_ingredient_unit'),
    )
    with op.batch_alter_table('ingredient_units', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_ingredient_units_ingredient_id'),
            ['ingredient_id'], unique=False,
        )

    # -----------------------------------------------------------------
    # 2) TICKET-3: new PurchaseInvoiceCharge table (empty; migrated below)
    # -----------------------------------------------------------------
    op.create_table(
        'purchase_invoice_charges',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('type_name', sa.String(length=60), nullable=False),
        sa.Column('is_percentage', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('rate_pct', sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column('amount_egp', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'),
        sa.Column('display_order', sa.Integer(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['invoice_id'], ['purchase_invoices.id'], name='fk_charge_invoice'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('purchase_invoice_charges', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_purchase_invoice_charges_invoice_id'),
            ['invoice_id'], unique=False,
        )

    # -----------------------------------------------------------------
    # 3) Audit-trail columns on line tables (nullable)
    # -----------------------------------------------------------------
    with op.batch_alter_table('medicine_dispenses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('input_qty', sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column('input_unit_code', sa.String(length=40), nullable=True))

    with op.batch_alter_table('purchase_lines', schema=None) as batch_op:
        batch_op.add_column(sa.Column('input_qty', sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column('input_unit_code', sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('input_unit_price', sa.Numeric(precision=12, scale=2), nullable=True))

    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.add_column(sa.Column('input_qty', sa.Numeric(precision=14, scale=3), nullable=True))
        batch_op.add_column(sa.Column('input_unit_code', sa.String(length=40), nullable=True))

    # -----------------------------------------------------------------
    # 4) DATA MIGRATION — before dropping old tax/discount columns
    # -----------------------------------------------------------------
    conn = op.get_bind()

    # 4a) Copy existing tax_type/tax_amount into purchase_invoice_charges
    conn.execute(sa.text("""
        INSERT INTO purchase_invoice_charges
            (invoice_id, kind, type_name, is_percentage, rate_pct, amount_egp, display_order)
        SELECT id, 'tax', COALESCE(tax_type, 'vat'), false, NULL, tax_amount, 0
        FROM purchase_invoices
        WHERE tax_amount IS NOT NULL AND tax_amount > 0
    """))
    conn.execute(sa.text("""
        INSERT INTO purchase_invoice_charges
            (invoice_id, kind, type_name, is_percentage, rate_pct, amount_egp, display_order)
        SELECT id, 'discount', COALESCE(discount_type, 'cash'), false, NULL, discount_amount, 0
        FROM purchase_invoices
        WHERE discount_amount IS NOT NULL AND discount_amount > 0
    """))

    # 4b) Seed a base-unit row per existing ingredient (factor=1)
    conn.execute(sa.text("""
        INSERT INTO ingredient_units
            (ingredient_id, unit_code, unit_label, factor_to_base, is_default_purchase)
        SELECT i.id, i.unit,
               CASE i.unit
                   WHEN 'kg' THEN 'كيلو'
                   WHEN 'litre' THEN 'لتر'
                   WHEN 'ml' THEN 'مل'
                   WHEN 'piece' THEN 'قطعة'
                   WHEN 'box' THEN 'علبة'
                   ELSE i.unit
               END,
               1, true
        FROM ingredients i
        WHERE NOT EXISTS (
            SELECT 1 FROM ingredient_units u
            WHERE u.ingredient_id = i.id AND u.unit_code = i.unit
        )
    """))

    # 4c) Backfill audit trail on existing purchase_lines / stock_movements
    #     (old data was already in base unit — factor 1 implicitly)
    conn.execute(sa.text("""
        UPDATE purchase_lines
        SET input_qty = qty,
            input_unit_code = (SELECT unit FROM ingredients WHERE id = purchase_lines.ingredient_id),
            input_unit_price = unit_price
        WHERE input_qty IS NULL
    """))
    conn.execute(sa.text("""
        UPDATE stock_movements
        SET input_qty = ABS(delta),
            input_unit_code = (SELECT unit FROM ingredients WHERE id = stock_movements.ingredient_id)
        WHERE input_qty IS NULL
    """))

    # -----------------------------------------------------------------
    # 5) Now safe to drop the old single-tax/discount columns
    # -----------------------------------------------------------------
    with op.batch_alter_table('purchase_invoices', schema=None) as batch_op:
        batch_op.drop_column('discount_amount')
        batch_op.drop_column('discount_type')
        batch_op.drop_column('tax_amount')
        batch_op.drop_column('tax_type')


def downgrade():
    with op.batch_alter_table('purchase_invoices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tax_type', sa.VARCHAR(length=60), nullable=True))
        batch_op.add_column(sa.Column('tax_amount', sa.NUMERIC(precision=14, scale=2), server_default=sa.text("'0'"), nullable=False))
        batch_op.add_column(sa.Column('discount_type', sa.VARCHAR(length=60), nullable=True))
        batch_op.add_column(sa.Column('discount_amount', sa.NUMERIC(precision=14, scale=2), server_default=sa.text("'0'"), nullable=False))

    with op.batch_alter_table('stock_movements', schema=None) as batch_op:
        batch_op.drop_column('input_unit_code')
        batch_op.drop_column('input_qty')

    with op.batch_alter_table('purchase_lines', schema=None) as batch_op:
        batch_op.drop_column('input_unit_price')
        batch_op.drop_column('input_unit_code')
        batch_op.drop_column('input_qty')

    with op.batch_alter_table('medicine_dispenses', schema=None) as batch_op:
        batch_op.drop_column('input_unit_code')
        batch_op.drop_column('input_qty')

    with op.batch_alter_table('purchase_invoice_charges', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_purchase_invoice_charges_invoice_id'))
    op.drop_table('purchase_invoice_charges')

    with op.batch_alter_table('ingredient_units', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ingredient_units_ingredient_id'))
    op.drop_table('ingredient_units')
