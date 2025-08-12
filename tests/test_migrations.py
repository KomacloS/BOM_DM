import sqlalchemy
from sqlmodel import create_engine
import app.main as main

def test_migration_adds_new_columns(tmp_path):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=sqlalchemy.pool.StaticPool)
    main.engine = engine
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text('CREATE TABLE bomitem (id INTEGER PRIMARY KEY, part_number TEXT, description TEXT, quantity INTEGER, reference TEXT)'))
    main.migrate_db()
    insp = sqlalchemy.inspect(engine)
    cols = {c['name'] for c in insp.get_columns('bomitem')}
    assert {'manufacturer','mpn','footprint','unit_cost','dnp','currency'}.issubset(cols)
    assert 'inventory' in insp.get_table_names()
    assert 'fxrate' in insp.get_table_names() or 'fxrates' in insp.get_table_names()


def test_migration_adds_created_at_columns():
    engine = create_engine(
        'sqlite:///:memory:',
        connect_args={'check_same_thread': False},
        poolclass=sqlalchemy.pool.StaticPool,
    )
    main.engine = engine
    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                'CREATE TABLE customer (id INTEGER PRIMARY KEY, name TEXT)'
            )
        )
        conn.execute(
            sqlalchemy.text(
                'CREATE TABLE project (id INTEGER PRIMARY KEY, customer_id INTEGER, code TEXT, title TEXT)'
            )
        )
    main.migrate_db()
    insp = sqlalchemy.inspect(engine)
    cust_cols = {c['name'] for c in insp.get_columns('customer')}
    proj_cols = {c['name'] for c in insp.get_columns('project')}
    assert 'created_at' in cust_cols
    assert 'created_at' in proj_cols
