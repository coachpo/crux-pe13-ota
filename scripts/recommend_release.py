#!/usr/bin/env python3
"""Select a prepared release after its matching GitHub asset upload completed."""

import argparse
from pathlib import Path
import re
import sys

from release_common import (FEED_PATH, ReleaseError, load_json, require,
                            validate_release, write_json)


def recommend(site_dir, release_id, uploaded_release):
    require(isinstance(release_id, str) and re.fullmatch(r"[0-9a-f]{64}", release_id),
            "Release id must be the prepared ZIP SHA256")
    record = load_json(site_dir / "releases" / (release_id + ".json"))
    validate_release(record)
    feed = record["feed"]
    require(uploaded_release.get("tagName") == record["tag"],
            "Uploaded release tag differs from the prepared release")
    assets = [asset for asset in uploaded_release.get("assets", [])
              if asset.get("name") == feed["filename"]]
    require(len(assets) == 1 and assets[0].get("size") == feed["size"]
            and assets[0].get("url") == feed["url"],
            "Upload the matching named ZIP asset before recommending it")
    if "state" in assets[0]:
        require(assets[0]["state"] == "uploaded", "Release asset upload is incomplete")
    if assets[0].get("digest") is not None:
        require(assets[0]["digest"] == "sha256:" + record["ota"]["sha256"],
                "Uploaded asset digest differs from the prepared ZIP")
    path = site_dir / FEED_PATH
    write_json(path, feed)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--uploaded-release-json", type=Path, required=True,
                        help="gh release view TAG --json tagName,assets output after upload")
    args = parser.parse_args()
    try:
        path = recommend(args.site_dir, args.release_id, load_json(args.uploaded_release_json))
    except (ReleaseError, OSError, KeyError, TypeError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print("Recommended release at " + str(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
