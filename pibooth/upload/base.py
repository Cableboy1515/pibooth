"""Base types shared by all photo upload backends."""

from dataclasses import dataclass


@dataclass
class UploadResult:
    """Result of a successful upload."""

    #: Album/share URL when the backend can provide one, ``None`` otherwise.
    url: str | None = None


class UploadBackend:
    """Base class for a photo upload backend.

    A fresh instance is created every time the relevant settings change
    (see :py:func:`pibooth.upload.create_backend`), so implementations can
    safely cache values read in :py:meth:`configure`.
    """

    #: Unique identifier, matches the ``upload_backend`` configuration choice
    id: str = "base"
    #: Human friendly name shown in the web interface
    label: str = "Base"

    def configure(self, cfg: object) -> None:
        """Read and validate the backend options from the configuration.

        :param cfg: :py:class:`pibooth.config.parser.PiConfigParser` instance
        :raises ValueError: if the configuration is missing or invalid
        """
        raise NotImplementedError

    def upload(self, filename: str) -> UploadResult:
        """Upload the given file, return the result.

        :param filename: absolute path of the file to upload
        :raises Exception: any error prevents the upload from succeeding,
            the caller is responsible for retrying
        """
        raise NotImplementedError

    def test(self) -> str:
        """Check that the backend is reachable and correctly configured.

        :return: human readable success message
        :raises Exception: with a human readable message on failure
        """
        raise NotImplementedError
