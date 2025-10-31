from logging.config import fileConfig
import os
from sqlalchemy import engine_from_config, pool, create_engine
from alembic import context
from sqlmodel import SQLModel
import app.main as models

config = context.config
# Our INI may omit logging sections; guard fileConfig to avoid KeyError
if config.config_file_name:
    try:
        fileConfig(config.config_file_name)
    except KeyError:
        pass

target_metadata = SQLModel.metadata

def _get_url() -> str:
    """Prefer DATABASE_URL env var; fall back to alembic.ini setting."""
    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    return config.get_main_option("sqlalchemy.url")

def run_migrations_offline():
    url = _get_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    url = _get_url()
    if url:
        connectable = create_engine(url, poolclass=pool.NullPool)
    else:
        connectable = engine_from_config(
            config.get_section(config.config_ini_section),
            prefix='sqlalchemy.',
            poolclass=pool.NullPool,
        )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
