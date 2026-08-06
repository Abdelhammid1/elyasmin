"""TREASURY: cash/bank accounts with tracked balances

Adds accounts, account_movements and account_transfers, plus a nullable
account_id on the four models that represent a cash event.

NO BACKFILL, deliberately. Historical payments and expenses keep
account_id = NULL and are excluded from every balance. Each account instead
starts at the opening balance the client actually counts on activation day —
that is the only figure they can reconcile against the money in the drawer.
Linking all history to a default "الخزنة الرئيسية" would produce a balance
that almost certainly disagrees with the real cash, and would need the opening
balance back-computed to hide the difference.

Revision ID: d4a7b21c6e83
Revises: b8e2f0c94d17
Create Date: 2026-08-06 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd4a7b21c6e83'
down_revision = 'b8e2f0c94d17'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'accounts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('account_type', sa.String(length=20), nullable=False, server_default='cash'),
        sa.Column('bank_name', sa.String(length=120), nullable=True),
        sa.Column('account_number', sa.String(length=60), nullable=True),
        sa.Column('opening_balance', sa.Numeric(precision=14, scale=2), nullable=False,
                  server_default='0'),
        sa.Column('current_balance', sa.Numeric(precision=14, scale=2), nullable=False,
                  server_default='0'),
        sa.Column('is_archived', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_account_user'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_account_name'),
    )
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_accounts_name'), ['name'], unique=True)

    op.create_table(
        'account_movements',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_id', sa.Integer(), nullable=False),
        sa.Column('movement_type', sa.String(length=20), nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('ref_type', sa.String(length=40), nullable=True),
        sa.Column('ref_id', sa.Integer(), nullable=True),
        sa.Column('moved_on', sa.Date(), nullable=False),
        sa.Column('notes', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name='fk_accmv_account'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_accmv_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('account_movements', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_movements_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_account_movements_movement_type'), ['movement_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_account_movements_moved_on'), ['moved_on'], unique=False)

    op.create_table(
        'account_transfers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('from_account_id', sa.Integer(), nullable=False),
        sa.Column('to_account_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column('transfer_date', sa.Date(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['from_account_id'], ['accounts.id'], name='fk_transfer_from'),
        sa.ForeignKeyConstraint(['to_account_id'], ['accounts.id'], name='fk_transfer_to'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_transfer_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('account_transfers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_account_transfers_from_account_id'), ['from_account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_account_transfers_to_account_id'), ['to_account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_account_transfers_transfer_date'), ['transfer_date'], unique=False)

    # account_id on every model that represents a cash event
    for table, fk in (
        ('supplier_payments', 'fk_supplierpayment_account'),
        ('customer_payments', 'fk_customerpayment_account'),
        ('expenses', 'fk_expense_account'),
        ('worker_payments', 'fk_workerpayment_account'),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('account_id', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f(f'ix_{table}_account_id'), ['account_id'], unique=False)
            batch_op.create_foreign_key(fk, 'accounts', ['account_id'], ['id'])


def downgrade():
    for table, fk in (
        ('worker_payments', 'fk_workerpayment_account'),
        ('expenses', 'fk_expense_account'),
        ('customer_payments', 'fk_customerpayment_account'),
        ('supplier_payments', 'fk_supplierpayment_account'),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(fk, type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{table}_account_id'))
            batch_op.drop_column('account_id')

    with op.batch_alter_table('account_transfers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_transfers_transfer_date'))
        batch_op.drop_index(batch_op.f('ix_account_transfers_to_account_id'))
        batch_op.drop_index(batch_op.f('ix_account_transfers_from_account_id'))
    op.drop_table('account_transfers')

    with op.batch_alter_table('account_movements', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_account_movements_moved_on'))
        batch_op.drop_index(batch_op.f('ix_account_movements_movement_type'))
        batch_op.drop_index(batch_op.f('ix_account_movements_account_id'))
    op.drop_table('account_movements')

    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_accounts_name'))
    op.drop_table('accounts')
