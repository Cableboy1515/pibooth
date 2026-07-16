"""One-time Google Photos authorization helper.

Run this on any computer with a web browser (it does not need to be the
photo booth itself): it opens the Google OAuth consent screen, then writes
the authorized user token next to the given client secret file. Copy both
JSON files into the booth configuration directory (or upload them through
the web interface "Uploads" page) as ``google_client_secret.json`` and
``google_token.json``.
"""

import argparse
import os.path as osp
import sys

#: Scope allowing upload + creation of app-owned albums only (see
#: pibooth.upload.google_photos for details on this restriction)
SCOPES = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]

DEFAULT_CLIENT_SECRET = osp.expanduser("~/.config/pibooth/google_client_secret.json")


def main() -> None:
    """Application entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "client_secret",
        nargs="?",
        default=DEFAULT_CLIENT_SECRET,
        help=f"path to the OAuth client secret JSON file (default: {DEFAULT_CLIENT_SECRET})",
    )
    args = parser.parse_args()

    if not osp.isfile(args.client_secret):
        print(f"Client secret file not found: {args.client_secret}", file=sys.stderr)
        print(
            "Create a Desktop app OAuth client with the Photos Library API enabled in the Google "
            "Cloud console, download its JSON file and pass its path to this command.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Missing dependency, install with 'pip install pibooth[gphotos]'", file=sys.stderr)
        raise SystemExit(1) from None

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    credentials = flow.run_local_server(port=0)

    token_path = osp.join(osp.dirname(osp.abspath(args.client_secret)), "google_token.json")
    with open(token_path, "w", encoding="utf-8") as fp:
        fp.write(credentials.to_json())

    print(f"\nToken written to {token_path}")
    print("Copy both JSON files into the booth configuration directory, or upload them via the web interface.")


if __name__ == "__main__":
    main()
