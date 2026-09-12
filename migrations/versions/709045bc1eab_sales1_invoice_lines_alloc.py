"""SALES-1 Part 2 — sales_invoices + sales_invoice_lines + allocations

Revision ID: 709045bc1eab
Revises: 9aa0d0eeda08
Create Date: 2026-09-12 15:15:00.000000

Three new tables for the general sales-invoice feature:

  sales_invoices                    — invoice header (cash/credit
                                      split, treasury FK, customer
                                      or walk-in)
  sales_invoice_lines               — mixed cow / inventory / free
                                      line items with cost/book-
                                      value snapshots for the JE
  sales_invoice_payment_allocations — mirror of PaymentAllocation,
                                      pinned to sales_invoices.id

No data seed here — the seed migration for CoA 4096/4030 ships
in Commit 3. Every column that carries a boolean uses
`server_default=sa.text('false')` for Postgres safety (per
postgres-safe-migration-seeds memory).
"""
from alembic import op
import sqlalchemy as sa


revision = '709045bc1eab'
down_revision = '9aa0d0eeda08'
branch_labels = None
depends_on = None


def upgrade():
    # -------------------- sales_invoices --------------------
    op.create_table(
        'sales_invoices',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=True),
        sa.Column('walkin_name', sa.String(length=120), nullable=True),
        sa.Column('invoice_number', sa.String(length=40), nullable=True),
        sa.Column('invoice_date', sa.Date(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False,
                  server_default='draft'),
        sa.Column('payment_type', sa.String(length=10), nullable=False,
                  server_default='cash'),
        sa.Column('treasury_account_id', sa.Integer(), nullable=True),
        sa.Column('cash_amount', sa.Numeric(14, 2), nullable=False,
                  server_default='0'),
        sa.Column('credit_amount', sa.Numeric(14, 2), nullable=False,
                  server_default='0'),
        sa.Column('grand_total', sa.Numeric(14, 2), nullable=False,
                  server_default='0'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_archived', sa.Boolean(), nullable=False,
                  server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.ForeignKeyConstraint(['treasury_account_id'], ['accounts.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('sales_invoices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sales_invoices_customer_id'),
                              ['customer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sales_invoices_invoice_number'),
                              ['invoice_number'], unique=False)
        batch_op.create_index(batch_op.f('ix_sales_invoices_invoice_date'),
                              ['invoice_date'], unique=False)

    # -------------------- sales_invoice_lines --------------------
    op.create_table(
        'sales_invoice_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False,
                  server_default='0'),
        sa.Column('line_kind', sa.String(length=10), nullable=False),
        sa.Column('cow_id', sa.Integer(), nullable=True),
        sa.Column('ingredient_id', sa.Integer(), nullable=True),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('qty', sa.Numeric(14, 3), nullable=False,
                  server_default='1'),
        sa.Column('unit_price', sa.Numeric(12, 2), nullable=False),
        sa.Column('line_total', sa.Numeric(14, 2), nullable=False),
        sa.Column('cow_book_value_snapshot', sa.Numeric(12, 2),
                  nullable=True),
        sa.Column('inv_avg_cost_snapshot', sa.Numeric(12, 4),
                  nullable=True),
        sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id']),
        sa.ForeignKeyConstraint(['cow_id'], ['cows.id']),
        sa.ForeignKeyConstraint(['ingredient_id'], ['ingredients.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('cow_id', name='uq_sil_cow'),
    )
    with op.batch_alter_table('sales_invoice_lines',
                              schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sales_invoice_lines_invoice_id'),
                              ['invoice_id'], unique=False)

    # -------------------- sales_invoice_payment_allocations --------------------
    op.create_table(
        'sales_invoice_payment_allocations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('invoice_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['payment_id'], ['customer_payments.id']),
        sa.ForeignKeyConstraint(['invoice_id'], ['sales_invoices.id']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('payment_id', 'invoice_id',
                            name='uq_sipa_payment_invoice'),
    )
    with op.batch_alter_table('sales_invoice_payment_allocations',
                              schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_sales_invoice_payment_allocations_payment_id'),
            ['payment_id'], unique=False,
        )
        batch_op.create_index(
            batch_op.f('ix_sales_invoice_payment_allocations_invoice_id'),
            ['invoice_id'], unique=False,
        )


def downgrade():
    with op.batch_alter_table('sales_invoice_payment_allocations',
                              schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(
            'ix_sales_invoice_payment_allocations_invoice_id'))
        batch_op.drop_index(batch_op.f(
            'ix_sales_invoice_payment_allocations_payment_id'))
    op.drop_table('sales_invoice_payment_allocations')

    with op.batch_alter_table('sales_invoice_lines',
                              schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sales_invoice_lines_invoice_id'))
    op.drop_table('sales_invoice_lines')

    with op.batch_alter_table('sales_invoices', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sales_invoices_invoice_date'))
        batch_op.drop_index(batch_op.f('ix_sales_invoices_invoice_number'))
        batch_op.drop_index(batch_op.f('ix_sales_invoices_customer_id'))
    op.drop_table('sales_invoices')
