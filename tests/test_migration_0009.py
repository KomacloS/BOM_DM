from pathlib import Path

import pytest

pytest.importorskip("alembic")
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


BASE_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = BASE_DIR / "migrations"
ALEMBIC_INI = MIGRATIONS_DIR / "alembic.ini"


def _alembic_config(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def test_migration_0009_handles_enum_and_python_table(tmp_path):
    db_path = tmp_path / "mig.db"
    url = f"sqlite:///{db_path}"
    cfg = _alembic_config(url)

    command.upgrade(cfg, "0008")
    engine = create_engine(url)

    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        conn.execute(
            text(
                "INSERT INTO customer (id, name, active, created_at) "
                "VALUES (1, 'Cust', 1, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO project (id, customer_id, code, title, name, status, priority, created_at) "
                "VALUES (1, 1, 'PRJ', 'Project', 'Project', 'draft', 'med', CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO assembly (id, project_id, rev, notes, created_at, test_mode) "
                "VALUES (1, 1, 'A', '', CURRENT_TIMESTAMP, 'non_powered')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO part (id, part_number, description, package, value, function, active_passive, "
                "power_required, datasheet_url, product_url, tol_p, tol_n, created_at) "
                "VALUES (1, 'P1', '', '', '', '', 'active', 0, '', '', '', '', CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(text("INSERT INTO testmacro (id, name) VALUES (1, 'Macro')"))
        conn.execute(
            text(
                "INSERT INTO bomitem (id, assembly_id, part_id, reference, qty, manufacturer, datasheet_url, "
                "unit_cost, currency, alt_part_number, is_fitted, notes) "
                "VALUES (1, 1, 1, 'R1', 1, '', '', 0, 'USD', '', 1, '')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO part_test_map (part_id, test_id, profile) "
                "VALUES (1, 1, 'PASSIVE')"
            )
        )
        conn.execute(text("DROP TABLE IF EXISTS pythontest"))
        conn.execute(text("PRAGMA foreign_keys=ON"))

    command.upgrade(cfg, "0009")

    insp = inspect(engine)
    assert "pythontest" in insp.get_table_names()

    columns = {col["name"]: col for col in insp.get_columns("part_test_map")}
    assert columns["test_id"]["nullable"] is True

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO pythontest (id, name) VALUES (2, 'Python Test')"))
        conn.execute(
            text(
                "INSERT INTO part (id, part_number, description, package, value, function, active_passive, "
                "power_required, datasheet_url, product_url, tol_p, tol_n, created_at) "
                "VALUES (2, 'P2', '', '', '', '', 'active', 0, '', '', '', '', CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO part_test_map (part_id, power_mode, profile, test_id, python_test_id, detail) "
                "VALUES (2, 'powered', 'PASSIVE', NULL, 2, 'Python detail')"
            )
        )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO part (id, part_number, description, package, value, function, active_passive, "
                "power_required, datasheet_url, product_url, tol_p, tol_n, created_at) "
                "VALUES (3, 'P3', '', '', '', '', 'active', 0, '', '', '', '', CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO part_test_map (part_id, power_mode, profile, detail) "
                    "VALUES (3, 'powered', 'PASSIVE', 'Invalid')"
                )
            )
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO bom_item_test_override (bom_item_id, power_mode, detail) "
                    "VALUES (1, 'powered', 'Invalid')"
                )
            )

    with engine.connect() as conn:
        mode = conn.execute(text("SELECT test_mode FROM assembly WHERE id = 1")).scalar()
        assert mode == "unpowered"
