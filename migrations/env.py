from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from shop_payments.config import DatabaseSettings

config = context.config
if config.config_file_name is not None:
    # Migrations also run inside the test process, where loggers created earlier must stay enabled.
    fileConfig(fname=config.config_file_name, disable_existing_loggers=False)

database_url = config.get_main_option(name="sqlalchemy.url") or DatabaseSettings().url

engine = create_engine(url=database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
