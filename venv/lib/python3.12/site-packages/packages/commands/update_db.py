"""
Pull from PyPI JSON API to local MongoDB instance.
"""
import itertools
import logging
import time
from itertools import chain, islice
from json.decoder import JSONDecodeError
from typing import Any, Callable, Iterable, Iterator, List, Optional

import click
import requests
import requests_cache
from lxml.html import fromstring as parse_html
from tqdm import tqdm
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.errors import BulkWriteError

from ._options import db_name_option
from ..network import make_session


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


def get_names(s: requests.Session) -> Iterator[str]:
    r = s.get('https://pypi.org/simple/')
    r.raise_for_status()

    doc = parse_html(r.text)
    doc.make_links_absolute('https://pypi.org/simple/', resolve_base_href=True)
    return get_project_names(doc)


def get_links(doc: Any) -> Iterator[str]:
    for item in doc.iterlinks():
        yield item[2]


def get_project_names(doc: Any) -> Iterator[str]:
    start = len('https://pypi.org/simple/')
    for link in get_links(doc):
        yield link[start:-1]


def query_pypi_with(s: requests.Session) -> Optional[Any]:
    def query_pypi(name: str) -> Optional[Any]:
        r = s.get(f'https://pypi.org/pypi/{name}/json')
        if r.status_code not in (200, 404):
            logger.warning('Error retrieving %s: %d', name, r.status_code)
            return None

        if r.status_code != 200:
            return None

        try:
            return r.json()
        except JSONDecodeError:
            logger.warning('Error decoding %s', name)
            return None

    return query_pypi


def drop_release(obj: Any) -> Any:
    """Drop 'releases' field, since pymongo doesn't support keys with
    '.' in the name.
    """
    # see: https://jira.mongodb.org/browse/SERVER-30575
    # see: https://jira.mongodb.org/browse/PYTHON-2000
    del obj['releases']
    return obj


def to_update(obj: Any) -> UpdateOne:
    """Create update operation for bulk write.
    """
    return UpdateOne(
        {"info.name": obj["info"]["name"]},
        {"$set": obj},
        upsert=True,
    )


def aggregate(
    iterable: Iterable[UpdateOne], size: int = 10
) -> Iterator[List[UpdateOne]]:
    """Break into chunks of at most `size`.
    """
    it = iter(iterable)
    try:
        while elt := next(it):
            yield list(chain((elt,), islice(it, size - 1)))
    except StopIteration:
        pass


def bulk_write_to(collection: Collection) -> Callable[[Iterable[Any]], Any]:
    def bulk_write(updates):
        try:
            return collection.bulk_write(updates)
        except BulkWriteError as e:
            logger.warning("Error writing values: %r", e.details)

    return bulk_write


@click.command("update-db")
@click.option("--limit", type=int, default=None, help="Limit on number of projects to update")
@db_name_option
def command(limit, db_name):
    """Populate/update database of JSON info for PyPI projects

    Persists HTTP cache in Redis and JSON data in MongoDB - both must have been
    started before calling this.
    """
    s = make_session()
    names = get_names(s)
    client = MongoClient()
    db = client[db_name]

    # Use list so tqdm shows progress.
    with_progress = tqdm(list(names)[:limit])
    data = map(query_pypi_with(s), with_progress)
    data = filter(None, data)
    data = map(drop_release, data)
    updates = map(to_update, data)
    bulk_updates = aggregate(updates)
    results = map(bulk_write_to(db.projects), bulk_updates)
    for result in results:
        pass
