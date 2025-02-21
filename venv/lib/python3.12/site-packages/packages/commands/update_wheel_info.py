"""
For each wheel file in local db, get the list of contained files.

wheel documents look like:

{
    "url": "https://...",
    "files": ["file1", "file2"],
}
"""
import logging
import zipfile
from functools import partial
from itertools import islice
from multiprocessing import Pool
from typing import Optional
from urllib.parse import urlparse

import click
import requests
from pymongo import MongoClient
from tqdm import tqdm

from ._options import db_name_option
from ..data import wheelfile_pipeline
from ..network import HttpFile, make_session


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("packages.network").setLevel(logging.WARNING)


# Worker globals.
session: Optional[requests.Session] = None
client: Optional[MongoClient] = None


def worker_init(request_rate: int) -> None:
    global client, session
    session = make_session(request_rate)
    client = MongoClient()


def process_url(url: str, db_name: str) -> None:
    f = HttpFile(url, session)
    parts = urlparse(url)
    obj = {
        "url": url,
    }
    with zipfile.ZipFile(f) as z:
        obj["files"] = z.namelist()
    db = client[default_db]
    # TODO: Bulk write
    # TODO: Update based on URL (to prevent duplicates)
    db.wheels.insert_one(obj)


@click.command("update-wheel-files")
@click.option("--limit", type=int, default=10)
@db_name_option
@click.option("--concurrency", type=int, default=None)
@click.option("--request-rate", type=int, default=300)
def command(limit, db_name, concurrency, request_rate):
    """Given a populated projects db, retrieve the list of files for each wheel.
    """
    rate = max(1, request_rate // concurrency)

    client = MongoClient()
    db = client[db_name]
    # TODO: Remove entries that already exist (by URL)
    files = list(db.projects.aggregate(wheelfile_pipeline))
    pool = Pool(concurrency, partial(worker_init, request_rate=rate))

    total = min(limit, len(files))
    with tqdm(total=total) as bar:
        for result in pool.imap_unordered(
            partial(process_url, db_name=db_name),
            (o["url"] for o in files[:limit]),
        ):
            bar.update(1)
