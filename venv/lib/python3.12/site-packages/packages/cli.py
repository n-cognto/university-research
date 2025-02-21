import click

from .commands import agg, list_wheel_files, update_db, update_wheel_info


@click.group()
def cli():
    pass


cli.add_command(agg.command)
cli.add_command(list_wheel_files.command)
cli.add_command(update_db.command)
cli.add_command(update_wheel_info.command)
