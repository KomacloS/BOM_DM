from alembic import op
import sqlalchemy as sa


def upgrade():
    # create parts table
    op.create_table(
        'part',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('number', sa.String(), nullable=False, unique=True),
        sa.Column('description', sa.Text, nullable=True),
    )
    conn = op.get_bind()
    # insert distinct part numbers and descriptions
    conn.execute(sa.text(
        "INSERT INTO part (number, description) "
        "SELECT DISTINCT part_number, description FROM bomitem"
    ))
    # update bomitem.part_id by joining on number and description
    conn.execute(sa.text(
        "UPDATE bomitem SET part_id = p.id FROM part p "
        "WHERE bomitem.part_number = p.number "
        "AND (bomitem.description IS NOT DISTINCT FROM p.description)"
    ))
    # add foreign key constraint
    op.create_foreign_key(
        'bomitem_part_id_fkey',
        'bomitem',
        'part',
        ['part_id'],
        ['id'],
        ondelete='SET NULL'
    )
    # enforce NOT NULL on part_number
    op.alter_column('bomitem', 'part_number', nullable=False)


def downgrade():
    op.alter_column('bomitem', 'part_number', nullable=True)
    op.drop_constraint('bomitem_part_id_fkey', 'bomitem', type_='foreignkey')
    op.drop_table('part')

