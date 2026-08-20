"""add base_branch to sessions

Revision ID: a1c3e7f92b40
Revises: 8b62df2749fc
Create Date: 2026-08-20 00:00:00.000000

Branch selection at session creation (claude/session-resume-plan.md's
follow-up): `branch_name` already existed but was only ever set later,
when a PR actually got opened (SessionWorker._record_pr_opened). It's now
set at creation time instead (the branch the agent works on, chosen or
named by the user up front), which makes `base_branch` -- the branch that
was branched *from* -- worth tracking too: needed by git_create_pr to
open the PR against the right base (not always the repo's overall
default), and by resume to know there's nothing special to reconstruct
(the working branch is already recorded in `branch_name`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1c3e7f92b40'
down_revision: Union[str, None] = '8b62df2749fc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('sessions', sa.Column('base_branch', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('sessions', 'base_branch')
