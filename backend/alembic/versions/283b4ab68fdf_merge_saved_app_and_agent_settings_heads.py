"""Merge saved_app and agent settings heads

Revision ID: 283b4ab68fdf
Revises: a1c7e3d9b542, f9a6c1e4d850
Create Date: 2026-11-27 17:31:16.848013

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '283b4ab68fdf'
down_revision: Union[str, Sequence[str], None] = ('a1c7e3d9b542', 'f9a6c1e4d850')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
