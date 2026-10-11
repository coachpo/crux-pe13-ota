#!/usr/bin/env python3
"""Stage accepted public files, optionally upload them, then recommend the release."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote
import zipfile

from prepare_release import prepare
from recommend_release import recommend
from release_common import (FEED_PATH, REPOSITORY, ReleaseError, file_identity,
                            load_json, release_url, require, write_json)
from validate_site import validate_site


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def public_file(path):
    path = path.resolve()
    require(path.is_file(), "Missing public file: " + str(path))
    require(path.suffix.lower() != ".pk8", "Private keys are not public assets")
    with path.open("rb") as stream:
        require(b"PRIVATE KEY-----" not in stream.read(4096),
                "Private keys are not public assets")
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            require(not {"META/misc_info.txt", "SYSTEM/build.prop"} <= set(names),
                    "Target-files archives must remain private")
            require(not any(name.lower().endswith(".pk8") for name in names),
                    "Private keys are not public assets")
            for entry in archive.infolist():
                if entry.filename.lower().endswith(".pem"):
                    with archive.open(entry) as stream:
                        require(b"PRIVATE KEY-----" not in stream.read(4096),
                                "Private keys are not public assets")
    return path


def matching_recovery(target_files, recovery):
    identity = file_identity(recovery)
    with zipfile.ZipFile(target_files) as archive:
        name = "IMAGES/recovery.img"
        require(archive.namelist().count(name) == 1,
                "Expected one Recovery image in signed target-files")
        digest = hashlib.sha256()
        with archive.open(name) as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(block)
        require(archive.getinfo(name).file_size == identity["bytes"]
                and digest.hexdigest() == identity["sha256"],
                "Public Recovery differs from signed target-files IMAGES/recovery.img")
    return identity


def stage(target_files, ota, recovery, notes, guide, extra_assets, site_dir,
          evidence_dir, tag, filename, repository=REPOSITORY,
          forum_url=None, website_url=None):
    evidence_dir = evidence_dir.resolve()
    require(not evidence_dir.is_relative_to(REPOSITORY_ROOT),
            "Keep staged binaries and publication evidence outside the OTA repository")
    # A canonical URL also validates the tag, repository and OTA asset basename.
    release_url(repository, tag, filename)
    sources = [(public_file(ota), filename), (public_file(recovery), recovery.name),
               (public_file(notes), notes.name), (public_file(guide), guide.name)]
    sources += [(public_file(path), path.name) for path in extra_assets]
    require(len({name for _, name in sources}) == len(sources),
            "Public asset filenames must be distinct")
    matching_recovery(target_files, recovery)
    notes.read_text(encoding="utf-8")
    guide.read_text(encoding="utf-8")
    validate_site(site_dir)
    ota_identity = file_identity(ota)
    for path in (site_dir / "releases").glob("*.json"):
        other = load_json(path)
        require(other.get("tag") != tag or other.get("release_id") == ota_identity["sha256"],
                "An immutable tag already identifies another retained release")
    record_path, record = prepare(target_files, ota, site_dir, tag, filename,
                                  repository, forum_url, website_url)
    assets_dir = evidence_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    for source, name in sources:
        identity = file_identity(source)
        destination = assets_dir / name
        if destination.exists():
            require(destination.is_file() and file_identity(destination) == identity,
                    "Staged asset already exists with different bytes: " + name)
        else:
            shutil.copyfile(source, destination)
        assets.append(dict(identity, name=name, path=str(destination),
                           url="https://github.com/" + repository + "/releases/download/"
                           + quote(tag, safe="") + "/" + quote(name, safe="")))
    plan = {"schema_version": 1, "repository": repository, "tag": tag,
            "release_id": record["release_id"], "record": str(record_path.resolve()),
            "assets": assets, "notes_file": str(assets_dir / notes.name),
            "notes_body": (assets_dir / notes.name).read_bytes().decode("utf-8"),
            "operations": [
                ["gh", "release", "create", tag, "--repo", repository,
                 "--title", tag, "--notes-file", str(assets_dir / notes.name), "--prerelease"],
                ["gh", "release", "upload", tag, "--repo", repository]
                + [asset["path"] for asset in assets],
                ["gh", "api", "repos/" + repository + "/releases/tags/" + quote(tag, safe="")]],
            "signature_acceptance": record["signature_acceptance"]}
    write_json(evidence_dir / "publication-plan.json", plan)
    validate_site(site_dir)
    return plan


class GitHub:
    def __init__(self, runner=subprocess.run):
        self.runner = runner

    def command(self, arguments):
        result = self.runner(["gh"] + arguments, capture_output=True, text=True)
        if result.returncode:
            raise ReleaseError(result.stderr.strip() or "GitHub command failed")
        return result.stdout

    def release(self, repository, tag):
        arguments = ["gh", "api", "repos/" + repository + "/releases/tags/" + quote(tag, safe="")]
        result = self.runner(arguments, capture_output=True, text=True)
        if result.returncode:
            if "HTTP 404" in result.stderr:
                return None
            raise ReleaseError(result.stderr.strip() or "Could not inspect the GitHub release")
        return json.loads(result.stdout)

    def verify_asset(self, repository, tag, expected, actual):
        require(actual.get("state") == "uploaded"
                and actual.get("size") == expected["bytes"]
                and actual.get("browser_download_url") == expected["url"],
                "Uploaded asset metadata differs: " + expected["name"])
        digest = actual.get("digest")
        if digest is not None:
            require(digest == "sha256:" + expected["sha256"],
                    "Uploaded asset digest differs: " + expected["name"])
        else:
            with tempfile.TemporaryDirectory(prefix="crux-release-verify-") as directory:
                self.command(["release", "download", tag, "--repo", repository,
                              "--pattern", expected["name"], "--dir", directory])
                require(file_identity(Path(directory) / expected["name"])
                        == {"bytes": expected["bytes"], "sha256": expected["sha256"]},
                        "Downloaded asset differs: " + expected["name"])


def check_release(release, plan):
    require(release.get("tag_name") == plan["tag"] and release.get("prerelease") is True
            and release.get("draft") is False,
            "Resume only the matching published prerelease; existing releases are never edited")
    expected_names = {asset["name"] for asset in plan["assets"]}
    require(all(asset.get("name") in expected_names for asset in release.get("assets", [])),
            "Existing immutable tag contains assets outside this publication plan")
    require((release.get("body") or "") == plan["notes_body"],
            "Existing immutable tag has different release notes")


def matching_assets(release, expected_assets):
    result = {}
    for expected in expected_assets:
        matches = [asset for asset in release.get("assets", [])
                   if asset.get("name") == expected["name"]]
        require(len(matches) <= 1, "Duplicate uploaded asset: " + expected["name"])
        if matches:
            result[expected["name"]] = matches[0]
    return result


def asset_identity(asset):
    return {name: asset.get(name) for name in
            ("id", "name", "state", "size", "digest", "browser_download_url", "updated_at")}


def publish(plan, site_dir, evidence_dir, github=None):
    github = github or GitHub()
    repository, tag = plan["repository"], plan["tag"]
    receipt = {"schema_version": 1, "status": "incomplete", "repository": repository,
               "tag": tag, "release_id": plan["release_id"], "uploaded": [], "reused": []}
    receipt_path = evidence_dir / "publication-receipt.json"
    try:
        # Recheck staged bytes on every retry before making any remote change.
        for asset in plan["assets"]:
            require(file_identity(Path(asset["path"]))
                    == {"bytes": asset["bytes"], "sha256": asset["sha256"]},
                    "Staged asset changed: " + asset["name"])
        require(Path(plan["notes_file"]).read_bytes().decode("utf-8") == plan["notes_body"],
                "Staged release notes differ from the frozen publication plan")
        validate_site(site_dir)
        release = github.release(repository, tag)
        if release is None:
            github.command(plan["operations"][0][1:])
            release = github.release(repository, tag)
            require(release is not None, "Created release is not visible")
        check_release(release, plan)
        existing = matching_assets(release, plan["assets"])
        verified = {}
        # Check every existing name before uploading anything; never clobber a mismatch.
        for asset in plan["assets"]:
            if asset["name"] in existing:
                github.verify_asset(repository, tag, asset, existing[asset["name"]])
                verified[asset["name"]] = asset_identity(existing[asset["name"]])
                receipt["reused"].append(asset["name"])
        for asset in plan["assets"]:
            if asset["name"] not in existing:
                github.command(["release", "upload", tag, "--repo", repository, asset["path"]])
                receipt["uploaded"].append(asset["name"])
                write_json(receipt_path, receipt)
        release = github.release(repository, tag)
        require(release is not None, "Uploaded release is not visible")
        check_release(release, plan)
        write_json(evidence_dir / "github-release.json", release)
        actual_assets = matching_assets(release, plan["assets"])
        require(len(actual_assets) == len(plan["assets"]), "Required public asset upload is missing")
        for asset in plan["assets"]:
            if asset_identity(actual_assets[asset["name"]]) != verified.get(asset["name"]):
                github.verify_asset(repository, tag, asset, actual_assets[asset["name"]])
        uploaded = {"tagName": tag, "assets": [
            {"name": asset["name"], "url": asset["browser_download_url"],
             "size": asset["size"], "state": asset["state"], "digest": asset.get("digest")}
            for asset in release["assets"]]}
        write_json(evidence_dir / "uploaded-release.json", uploaded)
        pointer = site_dir / FEED_PATH
        previous = pointer.read_bytes() if pointer.exists() else None
        try:
            recommend(site_dir, plan["release_id"], uploaded)
            receipt["site_validation"] = validate_site(site_dir)
            receipt["status"] = "published_and_recommended_locally"
            receipt["release_url"] = release.get("html_url")
            receipt["feed"] = str(pointer.resolve())
            write_json(receipt_path, receipt)
        except Exception:
            if previous is None:
                pointer.unlink(missing_ok=True)
            else:
                pointer.write_bytes(previous)
            raise
        return receipt
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = str(error)
        write_json(receipt_path, receipt)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--signed-target-files", type=Path, required=True)
    parser.add_argument("--ota", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--guide", type=Path, required=True)
    parser.add_argument("--extra-asset", type=Path, action="append", default=[])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--forum-url")
    parser.add_argument("--website-url")
    parser.add_argument("--publish", action="store_true",
                        help="create/resume the prerelease, upload and verify assets, then update the local feed")
    args = parser.parse_args()
    try:
        plan = stage(args.signed_target_files, args.ota, args.recovery,
                     args.release_notes, args.guide, args.extra_asset, args.site_dir,
                     args.evidence_dir, args.tag, args.filename, args.repository,
                     args.forum_url, args.website_url)
        print("Staged publication plan: " + str(args.evidence_dir / "publication-plan.json"))
        for operation in plan["operations"]:
            print(shlex.join(operation))
        if args.publish:
            publish(plan, args.site_dir, args.evidence_dir.resolve())
            print("Assets verified and local feed recommended. Review, commit and push the site to deploy Pages.")
        else:
            print("Prepare only. Re-run with --publish to create/resume and verify the prerelease before recommending it.")
    except (ReleaseError, OSError, KeyError, TypeError, ValueError, UnicodeError,
            zipfile.BadZipFile) as error:
        parser.exit(1, str(error) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
