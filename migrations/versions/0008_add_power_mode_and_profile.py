"""Add board test mode and test profile enums."""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_add_power_mode_and_profile"
down_revision = "0007_test_methods"
branch_labels = None
depends_on = None


def upgrade() -> None:
    test_mode_enum = sa.Enum("powered", "non_powered", name="test_mode_enum")
    test_profile_enum = sa.Enum("ACTIVE", "PASSIVE", name="test_profile_enum")
    bind = op.get_bind()
    test_mode_enum.create(bind, checkfirst=True)
    test_profile_enum.create(bind, checkfirst=True)

    with op.batch_alter_table("assembly") as batch:
        batch.add_column(
            sa.Column(
                "test_mode",
                test_mode_enum,
                nullable=False,
                server_default="powered",
            )
        )

    with op.batch_alter_table("part_test_map") as batch:
        batch.alter_column("test_macro_id", new_column_name="test_id")
        batch.add_column(
            sa.Column(
                "profile",
                test_profile_enum,
                nullable=False,
                server_default="PASSIVE",
            )
        )
        batch.drop_constraint("part_test_map_pkey", type_="primary")
        batch.create_primary_key("pk_part_test_map", ["part_id", "test_id", "profile"])
        batch.create_unique_constraint(
            "uq_part_test_map_part_test_profile", ["part_id", "test_id", "profile"]
        )
        batch.create_index("ptm_part_profile_idx", ["part_id", "profile"])

    op.execute(
        "UPDATE assembly SET test_mode = 'powered' WHERE test_mode IS NULL"
    )
    op.execute(
        "UPDATE part_test_map SET profile = 'PASSIVE' WHERE profile IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("part_test_map") as batch:
        batch.drop_index("ptm_part_profile_idx")
        batch.drop_constraint("uq_part_test_map_part_test_profile", type_="unique")
        batch.drop_constraint("pk_part_test_map", type_="primary")
        batch.drop_column("profile")
        batch.alter_column("test_id", new_column_name="test_macro_id")
        batch.create_primary_key("part_test_map_pkey", ["part_id", "test_macro_id"])

    with op.batch_alter_table("assembly") as batch:
        batch.drop_column("test_mode")

    test_profile_enum = sa.Enum("ACTIVE", "PASSIVE", name="test_profile_enum")
    test_mode_enum = sa.Enum("powered", "non_powered", name="test_mode_enum")
    bind = op.get_bind()
    test_profile_enum.drop(bind, checkfirst=True)
    test_mode_enum.drop(bind, checkfirst=True)
