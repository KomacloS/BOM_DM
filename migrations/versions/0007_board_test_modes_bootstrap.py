"""Board test modes bootstrap."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_board_test_modes_bootstrap"
down_revision = "0006_stub"
branch_labels = None
depends_on = None


def _create_enums(bind) -> tuple[sa.Enum, sa.Enum]:
    test_mode_enum = sa.Enum("powered", "unpowered", name="test_mode_enum")
    test_profile_enum = sa.Enum("active", "passive", name="test_profile_enum")
    test_mode_enum.create(bind, checkfirst=True)
    test_profile_enum.create(bind, checkfirst=True)
    return test_mode_enum, test_profile_enum


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    test_mode_enum, test_profile_enum = _create_enums(bind)

    assembly_columns = {col["name"] for col in insp.get_columns("assembly")}
    if "test_mode" not in assembly_columns:
        op.add_column(
            "assembly",
            sa.Column(
                "test_mode",
                test_mode_enum,
                nullable=False,
                server_default="unpowered",
            ),
        )
    else:
        op.alter_column(
            "assembly",
            "test_mode",
            existing_type=test_mode_enum,
            existing_nullable=False,
            server_default="unpowered",
        )

    op.execute(sa.text("UPDATE assembly SET test_mode = 'unpowered' WHERE test_mode = 'non_powered'"))
    op.execute(sa.text("UPDATE assembly SET test_mode = 'unpowered' WHERE test_mode IS NULL"))

    tables = insp.get_table_names()

    if "testmacro" not in tables:
        op.create_table(
            "testmacro",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False, unique=True),
            sa.Column("glb_path", sa.String(length=512), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
        )
    else:
        macro_cols = {col["name"] for col in insp.get_columns("testmacro")}
        if "glb_path" not in macro_cols:
            op.add_column("testmacro", sa.Column("glb_path", sa.String(length=512), nullable=True))
        if "notes" not in macro_cols:
            op.add_column("testmacro", sa.Column("notes", sa.Text, nullable=True))

    if "pythontest" not in tables:
        op.create_table(
            "pythontest",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(length=128), nullable=False, unique=True),
            sa.Column("file_path", sa.String(length=512), nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
        )
    else:
        py_cols = {col["name"] for col in insp.get_columns("pythontest")}
        if "file_path" not in py_cols:
            op.add_column("pythontest", sa.Column("file_path", sa.String(length=512), nullable=True))
        if "notes" not in py_cols:
            op.add_column("pythontest", sa.Column("notes", sa.Text, nullable=True))

    if "part_test_map" not in tables:
        op.create_table(
            "part_test_map",
            sa.Column("part_id", sa.Integer, sa.ForeignKey("part.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("power_mode", test_mode_enum, nullable=False, server_default="unpowered", primary_key=True),
            sa.Column("profile", test_profile_enum, nullable=False, primary_key=True),
            sa.Column("test_macro_id", sa.Integer, sa.ForeignKey("testmacro.id"), nullable=True),
            sa.Column("python_test_id", sa.Integer, sa.ForeignKey("pythontest.id"), nullable=True),
            sa.Column("detail", sa.Text, nullable=True),
            sa.CheckConstraint(
                "(test_macro_id IS NOT NULL AND python_test_id IS NULL) OR "
                "(test_macro_id IS NULL AND python_test_id IS NOT NULL)",
                name="ck_part_test_map_single_source",
            ),
        )
        op.create_index(
            "ix_part_test_map_part_profile_mode",
            "part_test_map",
            ["part_id", "profile", "power_mode"],
        )
        op.create_index(
            "ix_part_test_map_part_mode",
            "part_test_map",
            ["part_id", "power_mode"],
        )
    else:
        pt_cols = {col["name"] for col in insp.get_columns("part_test_map")}
        if "power_mode" not in pt_cols:
            op.add_column(
                "part_test_map",
                sa.Column("power_mode", test_mode_enum, nullable=False, server_default="unpowered"),
            )
            op.execute(sa.text("UPDATE part_test_map SET power_mode = 'unpowered' WHERE power_mode IS NULL"))
        if "profile" not in pt_cols:
            op.add_column(
                "part_test_map",
                sa.Column("profile", test_profile_enum, nullable=False, server_default="active"),
            )
        if "test_macro_id" not in pt_cols:
            op.add_column(
                "part_test_map",
                sa.Column("test_macro_id", sa.Integer, sa.ForeignKey("testmacro.id"), nullable=True),
            )
        if "python_test_id" not in pt_cols:
            op.add_column(
                "part_test_map",
                sa.Column("python_test_id", sa.Integer, sa.ForeignKey("pythontest.id"), nullable=True),
            )
        if "detail" not in pt_cols:
            op.add_column("part_test_map", sa.Column("detail", sa.Text, nullable=True))
        pk = insp.get_pk_constraint("part_test_map")
        desired_pk = {"part_id", "power_mode", "profile"}
        if set(pk.get("constrained_columns", [])) != desired_pk and bind.dialect.name != "sqlite":
            pk_name = pk.get("name")
            if pk_name:
                op.drop_constraint(pk_name, "part_test_map", type_="primary")
            op.create_primary_key("pk_part_test_map", "part_test_map", ["part_id", "power_mode", "profile"])
        checks = {c["name"] for c in insp.get_check_constraints("part_test_map")}
        if "ck_part_test_map_single_source" not in checks:
            op.create_check_constraint(
                "ck_part_test_map_single_source",
                "part_test_map",
                "(test_macro_id IS NOT NULL AND python_test_id IS NULL) OR "
                "(test_macro_id IS NULL AND python_test_id IS NOT NULL)",
            )
        existing_indexes = {idx["name"] for idx in insp.get_indexes("part_test_map")}
        if "ix_part_test_map_part_profile_mode" not in existing_indexes:
            op.create_index(
                "ix_part_test_map_part_profile_mode",
                "part_test_map",
                ["part_id", "profile", "power_mode"],
            )
        if "ix_part_test_map_part_mode" not in existing_indexes:
            op.create_index(
                "ix_part_test_map_part_mode",
                "part_test_map",
                ["part_id", "power_mode"],
            )

    if "bom_item_test_override" not in tables:
        op.create_table(
            "bom_item_test_override",
            sa.Column("bom_item_id", sa.Integer, sa.ForeignKey("bomitem.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("power_mode", test_mode_enum, nullable=False, server_default="unpowered", primary_key=True),
            sa.Column("test_macro_id", sa.Integer, sa.ForeignKey("testmacro.id"), nullable=True),
            sa.Column("python_test_id", sa.Integer, sa.ForeignKey("pythontest.id"), nullable=True),
            sa.Column("detail", sa.Text, nullable=True),
            sa.CheckConstraint(
                "(test_macro_id IS NOT NULL AND python_test_id IS NULL) OR "
                "(test_macro_id IS NULL AND python_test_id IS NOT NULL)",
                name="ck_bom_item_test_override_single_source",
            ),
        )
        op.create_index(
            "ix_bom_item_test_override_item_mode",
            "bom_item_test_override",
            ["bom_item_id", "power_mode"],
        )
    else:
        bo_cols = {col["name"] for col in insp.get_columns("bom_item_test_override")}
        if "power_mode" not in bo_cols:
            op.add_column(
                "bom_item_test_override",
                sa.Column("power_mode", test_mode_enum, nullable=False, server_default="unpowered"),
            )
            op.execute(sa.text("UPDATE bom_item_test_override SET power_mode = 'unpowered' WHERE power_mode IS NULL"))
        if "test_macro_id" not in bo_cols:
            op.add_column(
                "bom_item_test_override",
                sa.Column("test_macro_id", sa.Integer, sa.ForeignKey("testmacro.id"), nullable=True),
            )
        if "python_test_id" not in bo_cols:
            op.add_column(
                "bom_item_test_override",
                sa.Column("python_test_id", sa.Integer, sa.ForeignKey("pythontest.id"), nullable=True),
            )
        if "detail" not in bo_cols:
            op.add_column("bom_item_test_override", sa.Column("detail", sa.Text, nullable=True))
        checks = {c["name"] for c in insp.get_check_constraints("bom_item_test_override")}
        if "ck_bom_item_test_override_single_source" not in checks:
            op.create_check_constraint(
                "ck_bom_item_test_override_single_source",
                "bom_item_test_override",
                "(test_macro_id IS NOT NULL AND python_test_id IS NULL) OR "
                "(test_macro_id IS NULL AND python_test_id IS NOT NULL)",
            )
        existing_indexes = {idx["name"] for idx in insp.get_indexes("bom_item_test_override")}
        if "ix_bom_item_test_override_item_mode" not in existing_indexes:
            op.create_index(
                "ix_bom_item_test_override_item_mode",
                "bom_item_test_override",
                ["bom_item_id", "power_mode"],
            )

    with op.batch_alter_table("assembly") as batch:
        batch.alter_column("test_mode", server_default=None)

    fresh_tables = sa.inspect(bind).get_table_names()

    if "part_test_map" in fresh_tables:
        with op.batch_alter_table("part_test_map") as batch:
            batch.alter_column("power_mode", server_default=None, existing_type=test_mode_enum)
            batch.alter_column("profile", server_default=None, existing_type=test_profile_enum)

    if "bom_item_test_override" in fresh_tables:
        with op.batch_alter_table("bom_item_test_override") as batch:
            batch.alter_column("power_mode", server_default=None, existing_type=test_mode_enum)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "bom_item_test_override" in insp.get_table_names():
        op.drop_table("bom_item_test_override")

    if "pythontest" in insp.get_table_names():
        op.drop_table("pythontest")

    if "part_test_map" in insp.get_table_names():
        existing_indexes = {idx["name"] for idx in insp.get_indexes("part_test_map")}
        for name in ("ix_part_test_map_part_profile_mode", "ix_part_test_map_part_mode"):
            if name in existing_indexes:
                op.drop_index(name, table_name="part_test_map")
        if bind.dialect.name != "sqlite":
            checks = {c["name"] for c in insp.get_check_constraints("part_test_map")}
            if "ck_part_test_map_single_source" in checks:
                op.drop_constraint("ck_part_test_map_single_source", "part_test_map", type_="check")
            cols = {col["name"] for col in insp.get_columns("part_test_map")}
            if "detail" in cols:
                op.drop_column("part_test_map", "detail")
            if "python_test_id" in cols:
                op.drop_column("part_test_map", "python_test_id")
            if "test_macro_id" in cols:
                op.drop_column("part_test_map", "test_macro_id")
            if "profile" in cols:
                op.drop_column("part_test_map", "profile")
            if "power_mode" in cols:
                op.drop_column("part_test_map", "power_mode")

    assembly_columns = {col["name"] for col in insp.get_columns("assembly")}
    if "test_mode" in assembly_columns:
        op.drop_column("assembly", "test_mode")

    test_mode_enum = sa.Enum("powered", "unpowered", name="test_mode_enum")
    test_profile_enum = sa.Enum("active", "passive", name="test_profile_enum")
    test_mode_enum.drop(bind, checkfirst=True)
    test_profile_enum.drop(bind, checkfirst=True)
