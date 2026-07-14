import multiprocessing
import multiprocessing.pool
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from pibooth.pictures.factory import PictureFactory


class PicturesFactoryPool:
    def __init__(self) -> None:
        self._pool: multiprocessing.pool.Pool | None = None
        self._async_results: list[multiprocessing.pool.AsyncResult[Image.Image]] = []

    def add(self, factory: "PictureFactory") -> None:
        """Add a new picture factory and build it asyncronously."""
        if not self._pool:
            self._pool = multiprocessing.Pool(processes=min(multiprocessing.cpu_count(), 4))
        self._async_results.append(self._pool.apply_async(factory.build))

    def get(self) -> list[Image.Image]:
        """Return all the results."""
        return [res.get() for res in self._async_results]

    def clear(self) -> None:
        """Cancel all run tasks and drop all factories."""
        for res in self._async_results:
            res.get(5)
        self._async_results = []

    def quit(self) -> None:
        """Quit and cleanup the pool."""
        if self._pool:
            self._pool.terminate()
            self._pool.join()
