from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'complex_part_map',
        sa.Column('part_id', sa.Integer, sa.ForeignKey('part.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('complex_id', sa.Integer, sa.ForeignKey('complex.id', ondelete='CASCADE'), primary_key=True, nullable=False),
    )
    op.create_table(
        'pythontest_part_map',
        sa.Column('part_id', sa.Integer, sa.ForeignKey('part.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('pythontest_id', sa.Integer, sa.ForeignKey('pythontest.id', ondelete='CASCADE'), primary_key=True, nullable=False),
    )


def downgrade():
    op.drop_table('pythontest_part_map')
    op.drop_table('complex_part_map')
