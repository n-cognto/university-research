import click


db_name_option = click.option(
    "--db-name", default="pypi", help="Name of the database to use"
)
