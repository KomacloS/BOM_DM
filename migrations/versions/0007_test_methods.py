from alembic import op
import sqlalchemy as sa


def upgrade():
    op.create_table(
        'part_test_assignment',
        sa.Column('part_id', sa.Integer, sa.ForeignKey('part.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('method', sa.Enum('macro', 'python', 'quick_test', name='testmethod'), nullable=False, server_default='macro'),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
    )

    conn = op.get_bind()
    try:
        rows = conn.execute(sa.text('SELECT DISTINCT part_id FROM part_test_map')).fetchall()
    except Exception:
        rows = []
    for row in rows:
        part_id = row[0]
        try:
            conn.execute(
                sa.text(
                    "INSERT INTO part_test_assignment (part_id, method) VALUES (:pid, 'macro')"
                ),
                {"pid": part_id},
            )
        except Exception:
            continue


def downgrade():
    op.drop_table('part_test_assignment')

