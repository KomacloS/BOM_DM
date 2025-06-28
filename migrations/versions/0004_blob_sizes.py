from alembic import op
import sqlalchemy as sa
import os
from pathlib import Path

def upgrade():
    conn = op.get_bind()
    if 'size' not in [c['name'] for c in sa.inspect(conn).get_columns('blob')]:
        op.add_column('blob', sa.Column('size', sa.Integer))
    res = conn.execute(sa.text('SELECT sha256 FROM blob')).fetchall()
    for (sha,) in res:
        prefix = sha[:2]
        glob_path = Path('assets') / prefix
        for fname in glob_path.glob(f'{sha}.*'):
            conn.execute(sa.text('UPDATE blob SET size=:s WHERE sha256=:h'), {'s': fname.stat().st_size, 'h': sha})
            break
    op.create_index('ix_blob_size', 'blob', ['size'])

def downgrade():
    op.drop_index('ix_blob_size', table_name='blob')
    op.drop_column('blob', 'size')
