#!/usr/bin/env python3
"""Prepare public release history from an already accepted signed release pair."""

import argparse
from pathlib import Path
import sys
import zipfile

from release_common import (REPOSITORY, ReleaseError, file_identity, https_url,
                            load_json, paired_build, release_url, validate_release,
                            write_json)


def prepare(target_files, ota, site_dir, tag, filename, repository=REPOSITORY,
            forum_url=None, website_url=None):
    build = paired_build(target_files, ota)
    ota_identity = file_identity(ota, include_md5=True)
    feed = {"datetime": build["datetime"], "filename": filename,
            "id": ota_identity["sha256"], "size": ota_identity["bytes"],
            "url": release_url(repository, tag, filename), "version": build["version"],
            "filehash": ota_identity["md5"], "maintainers": [], "donate_url": None,
            "forum_url": https_url(forum_url, "Forum URL") if forum_url else None,
            "website_url": https_url(website_url, "Website URL") if website_url else None,
            "news_url": None}
    record = {"schema_version": 1, "release_id": feed["id"], "repository": repository,
              "tag": tag, "build": build, "ota": ota_identity, "feed": feed,
              "target_files": file_identity(target_files),
              "signature_acceptance": "Independent native signature gates required before upload"}
    validate_release(record)
    path = site_dir / "releases" / (feed["id"] + ".json")
    if path.exists():
        if load_json(path) != record:
            raise ReleaseError("An immutable release record already exists with different metadata")
    else:
        write_json(path, record)
    return path, record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signed-target-files", type=Path, required=True)
    parser.add_argument("--ota", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--forum-url")
    parser.add_argument("--website-url")
    args = parser.parse_args()
    try:
        path, record = prepare(args.signed_target_files, args.ota, args.site_dir,
                               args.tag, args.filename, args.repository,
                               args.forum_url, args.website_url)
    except (ReleaseError, OSError, KeyError, TypeError, ValueError, UnicodeError, zipfile.BadZipFile) as error:
        parser.exit(1, str(error) + "\n")
    print("Prepared " + str(path))
    print("Release id: " + record["release_id"])
    print("Upload the accepted ZIP first, then select this release using an uploaded asset record.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
