from alembic import op
import sqlalchemy as sa

def upgrade():
    op.create_table(
        'assembly',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('project_id', sa.Integer, sa.ForeignKey('project.id', ondelete='CASCADE')),
        sa.Column('rev', sa.String(length=16), nullable=False),
        sa.Column('vault_sha', sa.String(length=64), nullable=True),
        sa.Column('notes', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
    )
    op.add_column('bomitem', sa.Column('assembly_id', sa.Integer, sa.ForeignKey('assembly.id')))
    op.add_column('bomitem', sa.Column('part_id', sa.Integer, nullable=True))
    op.execute("INSERT INTO assembly (project_id, rev) SELECT id, 'A' FROM project")
    op.execute("UPDATE bomitem SET assembly_id = (SELECT assembly.id FROM assembly JOIN project ON assembly.project_id = project.id WHERE project.id = bomitem.project_id AND assembly.rev='A')")
    op.drop_constraint('bomitem_project_id_fkey', 'bomitem', type_='foreignkey')
    op.drop_column('bomitem', 'project_id')

def downgrade():
    op.add_column('bomitem', sa.Column('project_id', sa.Integer, sa.ForeignKey('project.id')))
    op.execute("UPDATE bomitem SET project_id = (SELECT project_id FROM assembly WHERE id = assembly_id)")
    op.drop_column('bomitem', 'assembly_id')
    op.drop_column('bomitem', 'part_id')
    op.drop_table('assembly')
