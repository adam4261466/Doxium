"""initial schema

Revision ID: a3d6b8422ed4
Revises: 
Create Date: 2026-01-31 13:49:29.724018

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3d6b8422ed4'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Use try/except per column so existing columns don't crash it
        pass

    # Use raw SQL with IF NOT EXISTS instead
    op.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(20)')
    op.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP')
    op.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS lemonsqueezy_subscription_id VARCHAR(100)')
    op.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS ls_customer_portal_url VARCHAR(500)')
    op.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_cancelled_at TIMESTAMP')


def downgrade():
    op.execute('ALTER TABLE users DROP COLUMN IF EXISTS subscription_status')
    op.execute('ALTER TABLE users DROP COLUMN IF EXISTS subscription_expires_at')
    op.execute('ALTER TABLE users DROP COLUMN IF EXISTS lemonsqueezy_subscription_id')
    op.execute('ALTER TABLE users DROP COLUMN IF EXISTS ls_customer_portal_url')
    op.execute('ALTER TABLE users DROP COLUMN IF EXISTS subscription_cancelled_at')
