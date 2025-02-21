import json
import sys

import click
from pymongo import MongoClient

from ._options import db_name_option


def iterable_json_adapter(iterable):
    class Adapter(list):
        def __bool__(self):
            return True

        def __iter__(self):
            return iterable

    return Adapter()


@click.command("agg")
@click.option("--collection", required=True)
@click.argument("file", type=click.File("rb"))
@db_name_option
def command(db_name, collection, file):
    query = json.load(file)
    client = MongoClient()
    collection = client[db_name][collection]
    result = collection.aggregate(query)
    json.dump(iterable_json_adapter(result), sys.stdout)
