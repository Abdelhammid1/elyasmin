"""custom ingredient categories + tax/discount on purchase invoices + supplier-customer link

Revision ID: 93016b312d3c
Revises: 4fb2c1025c4b
Create Date: 2026-07-30 22:39:10.902316

"""
from alembic import op
import sqlalchemy as sa


revision = '93016b312d3c'
down_revision = '4fb2c1025c4b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('linked_supplier_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_customers_linked_supplier_id'), ['linked_supplier_id'], unique=False)
        batch_op.create_foreign_key('fk_customers_linked_supplier_id', 'suppliers', ['linked_supplier_id'], ['id'])

    with op.batch_alter_table('ingredients', schema=None) as batch_op:
        batch_op.alter_column('category',
               existing_type=sa.VARCHAR(length=20),
               type_=sa.String(length=60),
               existing_nullable=False)

    with op.batch_alter_table('purchase_invoices', schema=None) as batch_op:
        batch_op.add_column(sa.Column('subtotal', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('tax_type', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('tax_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('discount_type', sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column('discount_amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0'))

    # Existing invoices had no tax/discount → subtotal = total (already computed)
    op.execute("UPDATE purchase_invoices SET subtotal = total WHERE subtotal = 0")

    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('linked_customer_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_suppliers_linked_customer_id'), ['linked_customer_id'], unique=False)
        batch_op.create_foreign_key('fk_suppliers_linked_customer_id', 'customers', ['linked_customer_id'], ['id'])


def downgrade():
    with op.batch_alter_table('suppliers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_suppliers_linked_customer_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_suppliers_linked_customer_id'))
        batch_op.drop_column('linked_customer_id')

    with op.batch_alter_table('purchase_invoices', schema=None) as batch_op:
        batch_op.drop_column('discount_amount')
        batch_op.drop_column('discount_type')
        batch_op.drop_column('tax_amount')
        batch_op.drop_column('tax_type')
        batch_op.drop_column('subtotal')

    with op.batch_alter_table('ingredients', schema=None) as batch_op:
        batch_op.alter_column('category',
               existing_type=sa.String(length=60),
               type_=sa.VARCHAR(length=20),
               existing_nullable=False)

    with op.batch_alter_table('customers', schema=None) as batch_op:
        batch_op.drop_constraint('fk_customers_linked_supplier_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_customers_linked_supplier_id'))
        batch_op.drop_column('linked_supplier_id')
