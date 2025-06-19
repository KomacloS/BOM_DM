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
