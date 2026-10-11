#!/usr/bin/env python3
"""Validate the public Pages artifact and its native update recommendation."""

import argparse
from pathlib import Path
import re
import sys

from release_common import (FEED_PATH, ReleaseError, load_json, require,
                            validate_feed, validate_release)


def validate_site(site_dir):
    require((site_dir / "index.html").is_file(), "Site requires its candidate index page")
    records = {}
    for path in site_dir.rglob("*"):
        require(not path.is_symlink(), "Pages content must be an ordinary public file")
        if path.is_dir():
            continue
        relative = path.relative_to(site_dir)
        if relative in {Path("index.html"), Path(".nojekyll"), FEED_PATH}:
            continue
        require(len(relative.parts) == 2 and relative.parts[0] == "releases"
                and re.fullmatch(r"[0-9a-f]{64}\.json", relative.name),
                "Unexpected public artifact: " + str(relative))
        record = load_json(path)
        validate_release(record)
        require(relative.stem == record["release_id"], "Release record filename differs from its identity")
        records[record["release_id"]] = record
    pointer = site_dir / FEED_PATH
    if pointer.exists():
        feed = load_json(pointer)
        validate_feed(feed)
        require(feed["id"] in records and feed == records[feed["id"]]["feed"],
                "Recommendation must match retained release history")
    return {"releases": len(records), "recommended": pointer.exists()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    args = parser.parse_args()
    try:
        result = validate_site(args.site_dir)
    except (ReleaseError, OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("Public site valid: " + str(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
