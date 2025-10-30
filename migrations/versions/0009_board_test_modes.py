"""Board test modes with powered/unpowered mappings."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_board_test_modes"
down_revision = "0008_add_power_mode_and_profile"
branch_labels = None
depends_on = None


def _rename_enum_value(bind, enum_name: str, old: str, new: str) -> None:
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TYPE {enum_name} RENAME VALUE '{old}' TO '{new}'")
    else:
        op.execute(
            sa.text(
                f"UPDATE assembly SET test_mode = :new WHERE test_mode = :old"
            ),
            {"old": old, "new": new},
        )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _rename_enum_value(bind, "test_mode_enum", "non_powered", "unpowered")

    if "pythontest" not in inspector.get_table_names():
        op.create_table(
            "pythontest",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("file_path", sa.Text, nullable=True),
            sa.Column("notes", sa.Text, nullable=True),
        )

    with op.batch_alter_table("assembly") as batch:
        batch.alter_column(
            "test_mode",
            existing_type=sa.Enum("powered", "unpowered", name="test_mode_enum"),
            server_default="unpowered",
        )

    with op.batch_alter_table("part_test_map") as batch:
        batch.add_column(
            sa.Column(
                "power_mode",
                sa.Enum("powered", "unpowered", name="test_mode_enum"),
                nullable=False,
                server_default="unpowered",
            )
        )
        batch.add_column(
            sa.Column(
                "python_test_id",
                sa.Integer,
                sa.ForeignKey("pythontest.id", ondelete="CASCADE"),
                nullable=True,
            )
        )
        batch.add_column(sa.Column("detail", sa.Text, nullable=True))
        batch.drop_index("ptm_part_profile_idx")
        batch.drop_constraint("uq_part_test_map_part_test_profile", type_="unique")
        batch.drop_constraint("pk_part_test_map", type_="primary")
        batch.alter_column(
            "test_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch.create_primary_key(
            "pk_part_test_map", ["part_id", "power_mode", "profile"]
        )
        batch.create_index(
            "ptm_part_profile_mode_idx", ["part_id", "profile", "power_mode"]
        )
        batch.create_index("ptm_part_power_mode_idx", ["part_id", "power_mode"])
        batch.create_check_constraint(
            "ck_part_test_map_exactly_one_test",
            "((CASE WHEN test_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN python_test_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
        )

    op.execute(
        sa.text(
            "UPDATE part_test_map SET power_mode = 'unpowered' WHERE power_mode IS NULL"
        )
    )

    with op.batch_alter_table("part_test_map") as batch:
        batch.alter_column(
            "power_mode",
            existing_type=sa.Enum("powered", "unpowered", name="test_mode_enum"),
            server_default=None,
        )

    op.create_table(
        "bom_item_test_override",
        sa.Column(
            "bom_item_id",
            sa.Integer,
            sa.ForeignKey("bomitem.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "power_mode",
            sa.Enum("powered", "unpowered", name="test_mode_enum"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "test_macro_id",
            sa.Integer,
            sa.ForeignKey("testmacro.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "python_test_id",
            sa.Integer,
            sa.ForeignKey("pythontest.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("detail", sa.Text, nullable=True),
        sa.CheckConstraint(
            "((CASE WHEN test_macro_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN python_test_id IS NOT NULL THEN 1 ELSE 0 END)) = 1",
            name="ck_bom_item_test_override_one_source",
        ),
    )
    op.create_index(
        "bom_item_override_mode_idx",
        "bom_item_test_override",
        ["bom_item_id", "power_mode"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index("bom_item_override_mode_idx", table_name="bom_item_test_override")
    op.drop_table("bom_item_test_override")

    with op.batch_alter_table("part_test_map") as batch:
        batch.drop_index("ptm_part_power_mode_idx")
        batch.drop_index("ptm_part_profile_mode_idx")
        batch.drop_constraint("ck_part_test_map_exactly_one_test", type_="check")
        batch.drop_constraint("pk_part_test_map", type_="primary")
        batch.drop_column("detail")
        batch.drop_column("python_test_id")
        batch.drop_column("power_mode")
        batch.create_primary_key(
            "pk_part_test_map", ["part_id", "test_id", "profile"]
        )
        batch.create_unique_constraint(
            "uq_part_test_map_part_test_profile",
            ["part_id", "test_id", "profile"],
        )
        batch.create_index("ptm_part_profile_idx", ["part_id", "profile"])

    with op.batch_alter_table("assembly") as batch:
        batch.alter_column(
            "test_mode",
            existing_type=sa.Enum("powered", "unpowered", name="test_mode_enum"),
            server_default="powered",
        )

    _rename_enum_value(bind, "test_mode_enum", "unpowered", "non_powered")
