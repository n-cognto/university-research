import io
import logging
import time
import zipfile

import requests
import requests_cache
from cached_property import cached_property


logger = logging.getLogger(__name__)


allowable_codes=(200,404,)
requests_cache.install_cache(
    'request_cache',
    backend='redis',
    allowable_codes=allowable_codes
)


def make_throttle_hook(per_second=1):
    """
    Returns a response hook function which sleeps for `timeout` seconds if
    response is not cached
    """
    last_request_time = 0
    rate = 1 / per_second
    def hook(response, *args, **kwargs):
        nonlocal last_request_time
        if not getattr(response, 'from_cache', False):
            now = time.time()
            diff = now - last_request_time
            last_request_time = now
            if diff < rate:
                time.sleep(rate - diff)
        return response
    return hook


def make_session(rate: int = 300) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        'User-Agent': 'CLI library info getter. Email: chrahunt@gmail.com'
    })

    s.hooks['response'].append(make_throttle_hook(rate))
    return s


class HttpFile:
    """Lazy-downloaded file based on HTTP Range requests.

    Doesn't handle mutable resources (see Range-If). Since PyPI files are immutable,
    this is OK for our use case.
    """
    def __init__(self, url: str, s: requests.Session):
        self._url = url
        self._s = s
        self._offset = 0

    def read(self, count=None):
        if count is None:
            end = self._size - 1
        else:
            end = self._offset + count - 1

        headers = {
            "Range": f"bytes={self._offset}-{end}"
        }

        r = self._s.get(self._url, headers=headers)
        data = r.content
        self._offset += len(data)
        return data

    def seek(self, offset, whence=0):
        if whence == 0:
            self._offset = offset
        elif whence == 1:
            self._offset += offset
        elif whence == 2:
            self._offset = self._size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")

    def seekable(self):
        return True

    def tell(self):
        return self._offset

    @cached_property
    def _size(self):
        return int(self._headers["Content-Length"])

    @cached_property
    def _headers(self):
        return self._s.head(self._url).headers
