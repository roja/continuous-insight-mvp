"""add_deleted_at_columns

Revision ID: 93ea8ee57d01
Revises: add_deleted_at_columns
Create Date: 2024-10-25 16:51:55.367583

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93ea8ee57d01'
down_revision: Union[str, None] = 'add_deleted_at_columns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
