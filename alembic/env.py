import os
import sys
from logging.config import fileConfig

from sqlalchemy import create_engine
from sqlalchemy import pool, inspect

from alembic import context

# Add the project root directory to the Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import models
from db_models import Base

# This is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def include_object(object, name, type_, reflected, compare_to):
    return True

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    print(f"\nOffline mode - URL: {url}")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    database_url = config.get_main_option("sqlalchemy.url")
    if not database_url:
        database_url = 'sqlite:///tech_audit.db'
    print(f"\nUsing database URL: {database_url}")

    # Print absolute path for SQLite database
    if database_url.startswith("sqlite:///"):
        db_file = database_url.replace("sqlite:///", "")
        abs_db_path = os.path.abspath(db_file)
        print(f"Using SQLite database file at: {abs_db_path}")

    # Create the engine with isolation_level="AUTOCOMMIT"
    connectable = create_engine(
        database_url,
        poolclass=pool.NullPool,
        isolation_level="AUTOCOMMIT",  # Add this line
    )

    with connectable.connect() as connection:
        # Debug database state
        inspector = inspect(connection)
        existing_tables = inspector.get_table_names()
        print(f"Existing tables in database: {existing_tables}")

        # Adjust Alembic configuration for SQLite
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_object=include_object,
            render_as_batch=True,
            transactional_ddl=False,        # Add this line
            transaction_per_migration=False, # Add this line
        )

        # Remove the 'with context.begin_transaction()' block
        context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
