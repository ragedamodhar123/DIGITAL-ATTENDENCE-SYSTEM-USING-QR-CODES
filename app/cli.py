import click
from sqlalchemy.exc import SQLAlchemyError

from .services.db_service import database_info

MIN_VERSION = 150000  # PostgreSQL 15


def register_cli(app):
    @app.cli.command("check-db")
    def check_db():
        """Connect to the database and print what we're connected to."""
        try:
            info = database_info()
        except SQLAlchemyError as e:
            raise click.ClickException(f"Cannot connect to the database: {e.__class__.__name__}: {e.orig}")
        major = info["server_version_num"] // 10000
        click.echo(f"Connected to '{info['database']}' as '{info['user']}' (PostgreSQL {major}).")
        if info["server_version_num"] < MIN_VERSION:
            click.echo("Warning: PostgreSQL 15 or newer is recommended.")
