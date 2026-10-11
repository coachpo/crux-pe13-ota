"""Public Crux release metadata shared by the offline preparation commands."""

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlparse
import zipfile


DEVICE = "crux"
VERSION = "thirteen_plus"
REPOSITORY = "coachpo/crux-pe13-ota"
FEED_PATH = Path("updates") / DEVICE / (VERSION + ".json")
IMAGE_NAMES = {"boot", "recovery", "system", "vendor", "dtbo", "vbmeta"}
OPTIONAL_URLS = ("donate_url", "forum_url", "website_url", "news_url")


class ReleaseError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def properties(body):
    result = {}
    for line in body.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        require(key not in result or result[key] == value,
                "Conflicting metadata property: " + key)
        result[key] = value
    return result


def archive_text(archive, name):
    require(archive.namelist().count(name) == 1,
            "Expected one archive entry: " + name)
    return archive.read(name).decode("utf-8")


def positive_integer(value, label):
    require(isinstance(value, str) and value.isdecimal() and int(value) > 0,
            label + " must be a positive integer")
    return int(value)


def file_identity(path, include_md5=False):
    sha256 = hashlib.sha256()
    md5 = hashlib.md5() if include_md5 else None
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            size += len(block)
            sha256.update(block)
            if md5 is not None:
                md5.update(block)
    result = {"bytes": size, "sha256": sha256.hexdigest()}
    if md5 is not None:
        result["md5"] = md5.hexdigest()
    return result


def https_url(value, label):
    require(isinstance(value, str), label + " must be an HTTPS URL")
    parsed = urlparse(value)
    require(parsed.scheme == "https" and parsed.netloc and not parsed.username
            and not parsed.password, label + " must be an HTTPS URL")
    return value


def release_url(repository, tag, filename):
    require(isinstance(repository, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", repository),
            "Repository must be OWNER/REPOSITORY")
    require(isinstance(tag, str) and tag and not any(char.isspace() for char in tag),
            "Release tag must be nonempty and contain no whitespace")
    require(isinstance(filename, str) and Path(filename).name == filename
            and filename.endswith(".zip"), "Filename must be a ZIP asset basename")
    return "https://github.com/" + repository + "/releases/download/" + quote(tag, safe="") + "/" + quote(filename, safe="")


def check_signature_envelope(path):
    """Check the Android ZIP envelope; trusted-key crypto remains a release gate."""
    size = path.stat().st_size
    require(size >= 28, "OTA lacks a whole-file signature envelope")
    with path.open("rb") as stream:
        stream.seek(-6, 2)
        footer = stream.read(6)
        signature_start = int.from_bytes(footer[:2], "little")
        comment_size = int.from_bytes(footer[4:], "little")
        require(footer[2:4] == b"\xff\xff" and 6 < signature_start <= comment_size
                and comment_size + 22 <= size, "Invalid whole-file signature footer")
        stream.seek(-comment_size - 22, 2)
        eocd = stream.read(comment_size + 22)
    require(eocd[:4] == b"PK\x05\x06" and int.from_bytes(eocd[20:22], "little") == comment_size
            and b"PK\x05\x06" not in eocd[4:], "Invalid signed ZIP comment")


def paired_build(target_files, ota):
    with zipfile.ZipFile(target_files) as archive:
        props = properties(archive_text(archive, "SYSTEM/build.prop"))
        misc = properties(archive_text(archive, "META/misc_info.txt"))
        names = archive.namelist()
        require(misc.get("ab_update", "false") == "false"
                and misc.get("use_dynamic_partitions", "false") == "false",
                "Expected the static non-A/B public product")
        for partition in IMAGE_NAMES:
            require(names.count("IMAGES/" + partition + ".img") == 1,
                    "Missing same-release image: " + partition)
        require("IMAGES/userdata.img" not in names,
                "A data-preserving release cannot include a fixed userdata image")
        fstab = archive_text(archive, "RECOVERY/RAMDISK/system/etc/recovery.fstab")
        rows = [line.split() for line in fstab.splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
        require(all(len(row) >= 5 for row in rows), "Invalid Recovery fstab row")
        data_rows = [row for row in rows if row[1] == "/data"]
        metadata_rows = [row for row in rows if row[1] == "/metadata"]
        require(len(data_rows) == 1 and len(metadata_rows) == 1,
                "Expected one public userdata and metadata mapping")
        data, metadata_row = data_rows[0], metadata_rows[0]
        options = data[4].split(",")
        require(data[0] in {"/dev/block/by-name/userdata", "/dev/block/bootdevice/by-name/userdata"}
                and data[2] == "ext4"
                and [option for option in options if option.startswith("fileencryption=")] == ["fileencryption=ice::v2"]
                and [option for option in options if option.startswith("keydirectory=")] == ["keydirectory=/metadata/vold/metadata_encryption"]
                and metadata_row[0] in {"/dev/block/by-name/metadata", "/dev/block/bootdevice/by-name/metadata"}
                and metadata_row[2] == "ext4",
                "Expected the public ext4/v2 userdata and metadata key-directory contract")
    require(props.get("org.pixelexperience.device") == DEVICE
            and props.get("ro.product.system.device") == DEVICE,
            "Target-files is not the Crux product")
    require(props.get("ro.build.type") == "user"
            and props.get("ro.secure") == "1"
            and props.get("ro.debuggable") == "0"
            and props.get("ro.adb.secure") == "1",
            "Expected the authenticated public user profile")
    require(props.get("org.pixelexperience.version") == VERSION
            and props.get("ro.build.version.release") == "13",
            "Expected the native Crux PE13 Plus version")
    incremental = props.get("ro.build.version.incremental", "")
    fingerprint = props.get("ro.build.fingerprint", "")
    require(incremental and fingerprint.endswith("/" + incremental + ":user/release-keys"),
            "Target-files must have coherent release-key build identity")
    timestamp = positive_integer(props.get("ro.build.date.utc"), "Android build timestamp")
    pe_timestamp = positive_integer(props.get("org.pixelexperience.build_date_utc"), "PE build timestamp")
    check_signature_envelope(ota)
    with zipfile.ZipFile(ota) as archive:
        metadata = properties(archive_text(archive, "META-INF/com/android/metadata"))
        names = archive.namelist()
        require(metadata.get("ota-type") == "BLOCK" and metadata.get("pre-device") == DEVICE,
                "Expected a full non-A/B Crux OTA")
        require("pre-build" not in metadata and "pre-build-incremental" not in metadata
                and metadata.get("ota-wipe") != "yes" and "payload.bin" not in names,
                "Expected a full data-preserving OTA")
        require(metadata.get("post-build") == fingerprint
                and metadata.get("post-build-incremental") == incremental,
                "OTA and target-files build identities differ")
        require(positive_integer(metadata.get("post-timestamp"), "OTA timestamp") == timestamp,
                "OTA and target-files Android timestamps differ")
        require(names.count("META-INF/com/android/otacert") == 1
                and archive.getinfo("META-INF/com/android/otacert").file_size > 0,
                "OTA lacks the public verification certificate")
    return {"device": DEVICE, "version": VERSION, "incremental": incremental,
            "fingerprint": fingerprint, "datetime": pe_timestamp,
            "ota_timestamp": timestamp, "profile": "user/release-keys"}


def validate_feed(feed):
    require(isinstance(feed, dict), "PE requires one update object")
    require(type(feed.get("datetime")) is int and feed["datetime"] > 0,
            "Feed datetime must be the PE build epoch")
    require(type(feed.get("size")) is int and feed["size"] > 0,
            "Feed size must be the uploaded ZIP byte count")
    require(feed.get("version") == VERSION, "Feed version must match the installed native version")
    require(isinstance(feed.get("id"), str) and re.fullmatch(r"[0-9a-f]{64}", feed["id"]),
            "Feed id must be the ZIP SHA256")
    require(isinstance(feed.get("filehash"), str) and re.fullmatch(r"[0-9a-f]{32}", feed["filehash"]),
            "Feed filehash must retain lowercase MD5 compatibility")
    https_url(feed.get("url"), "Feed URL")
    filename = feed.get("filename")
    require(isinstance(filename, str) and Path(filename).name == filename
            and filename.endswith(".zip"), "Feed filename must identify the ZIP asset")
    require(isinstance(feed.get("maintainers"), list), "Feed maintainers must be an array")
    for name in OPTIONAL_URLS:
        require(name in feed, "Missing native optional URL value: " + name)
        if feed[name] is not None:
            https_url(feed[name], name)


def validate_release(record):
    require(isinstance(record, dict) and record.get("schema_version") == 1,
            "Unsupported release record")
    validate_feed(record.get("feed"))
    feed, build, ota = record["feed"], record["build"], record["ota"]
    require(record.get("release_id") == feed["id"] == ota["sha256"], "Release identity differs from feed")
    require(feed["size"] == ota["bytes"] and feed["filehash"] == ota["md5"],
            "Release byte identity differs from feed")
    require(feed["version"] == build["version"] and feed["datetime"] == build["datetime"]
            and build["device"] == DEVICE and build["profile"] == "user/release-keys",
            "Release build differs from feed")
    expected = release_url(record["repository"], record["tag"], feed["filename"])
    mirror = record.get("temporary_mirror_url")
    if mirror is None:
        require(feed["url"] == expected,
                "Release URL must identify the immutable tag and asset")
    else:
        # Explicit, record-level escape hatch for a smoke run that must serve the
        # identical signed asset from another HTTPS origin. Never set implicitly.
        require(feed["url"] == mirror, "Feed URL must match the recorded temporary mirror")
        require(https_url(mirror, "Temporary mirror URL").startswith("https://"),
                "Temporary mirror URL must be HTTPS")
        require(Path(urlparse(mirror).path).name == feed["filename"],
                "Temporary mirror URL must serve the recorded asset basename")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))
