"""add deleted_at columns

Revision ID: add_deleted_at_columns
Revises: <previous_revision_id>
Create Date: 2024-10-25

"""
from alembic import op
import sqlalchemy as sa
from datetime import timezone

# revision identifiers, used by Alembic.
revision = 'add_deleted_at_columns'
down_revision = None  # Replace with your previous migration ID
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add deleted_at column to users table
    op.add_column('users', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))
    
    # Add deleted_at column to audits table
    op.add_column('audits', sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True))

def downgrade() -> None:
    # Remove deleted_at column from users table
    op.drop_column('users', 'deleted_at')
    
    # Remove deleted_at column from audits table
    op.drop_column('audits', 'deleted_at')
