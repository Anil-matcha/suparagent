"""Join the spend-ledger migration and the agent-owned-services migrations into one history.

Both branched from d4b8e1f7a936. A database that already has the agent-service tables upgrades through the spend
ledger first; one that has the spend ledger upgrades through the agent-service tables. Nothing else changes.

Revision ID: a1f4d7c2e856
Revises: a8c2e5f1b374, f6b2d8c4e013
"""

revision = "a1f4d7c2e856"
down_revision = ("a8c2e5f1b374", "f6b2d8c4e013")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
