from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'testmacro',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('glb_path', sa.Text, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
    )
    op.create_table(
        'complex',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('eda_path', sa.Text, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
    )
    op.create_table(
        'pythontest',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('file_path', sa.Text, nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
    )
    op.create_table(
        'part_test_map',
        sa.Column('part_id', sa.Integer, sa.ForeignKey('part.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('test_macro_id', sa.Integer, sa.ForeignKey('testmacro.id', ondelete='CASCADE'), primary_key=True, nullable=False),
    )


def downgrade():
    op.drop_table('part_test_map')
    op.drop_table('pythontest')
    op.drop_table('complex')
    op.drop_table('testmacro')
