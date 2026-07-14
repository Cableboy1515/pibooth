"""Script to list all installed fonts."""

from collections.abc import Iterable
from itertools import islice, zip_longest

from pibooth.fonts import get_available_fonts
from pibooth.utils import LOGGER, configure_logging


def take(n: int, iterable: Iterable[str]) -> list[str]:
    """Return first n items of the iterable as a list."""
    return list(islice(iterable, n))


def print_columns_words(words: list[str], column_count: int = 3) -> None:
    """Print a list of words into columns."""
    per_column, dangling = divmod(len(words), column_count)
    iter_words = iter(words)
    columns = [take(per_column + (dangling > i), iter_words) for i in range(column_count)]
    paddings = [max(map(len, column)) for column in columns]
    for row in zip_longest(*columns, fillvalue=""):
        print("  ".join(word.ljust(pad) for word, pad in zip(row, paddings)))


def main() -> None:
    """Application entry point."""
    configure_logging()
    LOGGER.info("Listing all fonts available...")

    print_columns_words(get_available_fonts(), 3)


if __name__ == "__main__":
    main()
