import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from prepare_release import prepare
from recommend_release import recommend
from release_common import FEED_PATH, ReleaseError, validate_feed, write_json
from validate_site import validate_site


class ReleaseServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "index.html").write_text("<!doctype html><title>Crux candidates</title>")
        self.target_files = self.root / "signed-target_files.zip"
        self.ota = self.root / "ota.zip"
        self.fingerprint = "Xiaomi/crux/crux:13/TQ3A.230901.001.B1/2026101102:user/release-keys"
        self.props = {
            "ro.product.system.device": "crux",
            "org.pixelexperience.device": "crux",
            "ro.build.type": "user",
            "ro.secure": "1",
            "ro.debuggable": "0",
            "ro.adb.secure": "1",
            "ro.build.version.release": "13",
            "ro.build.version.incremental": "2026101102",
            "ro.build.fingerprint": self.fingerprint,
            "ro.build.date.utc": "1791540000",
            "org.pixelexperience.version": "thirteen_plus",
            "org.pixelexperience.build_date_utc": "1791540240",
        }
        self.metadata = {
            "ota-type": "BLOCK",
            "pre-device": "crux",
            "post-build": self.fingerprint,
            "post-build-incremental": "2026101102",
            "post-timestamp": "1791540000",
        }
        self.make_target_files()
        self.make_ota()

    @staticmethod
    def property_text(values):
        return "".join(key + "=" + value + "\n" for key, value in values.items())

    def make_target_files(self, fstab=None, misc=None):
        if fstab is None:
            fstab = ("/dev/block/by-name/metadata /metadata ext4 defaults wait,first_stage_mount\n"
                     "/dev/block/bootdevice/by-name/userdata /data ext4 defaults wait,fileencryption=ice::v2,keydirectory=/metadata/vold/metadata_encryption\n")
        with zipfile.ZipFile(self.target_files, "w") as archive:
            archive.writestr("SYSTEM/build.prop", self.property_text(self.props))
            archive.writestr("META/misc_info.txt", self.property_text(misc or {}))
            archive.writestr("RECOVERY/RAMDISK/system/etc/recovery.fstab", fstab)
            for partition in ("boot", "recovery", "system", "vendor", "dtbo", "vbmeta"):
                archive.writestr("IMAGES/" + partition + ".img", b"fixture-image")

    def make_ota(self, signed=True):
        with zipfile.ZipFile(self.ota, "w") as archive:
            archive.writestr("META-INF/com/android/metadata", self.property_text(self.metadata))
            archive.writestr("META-INF/com/android/otacert", "fixture-public-certificate")
            archive.writestr("system.new.dat.br", b"fixture-update-data")
            if signed:
                # This fixture covers the envelope, not native signature acceptance.
                signature = b"fixture-pkcs7-envelope"
                size = len(signature) + 6
                archive.comment = signature + size.to_bytes(2, "little") + b"\xff\xff" + size.to_bytes(2, "little")

    def prepare(self, **kwargs):
        return prepare(self.target_files, self.ota, self.site, "r4-2026101102",
                       "Crux-PE13-R4.zip", **kwargs)

    @staticmethod
    def uploaded(record):
        return {"tagName": record["tag"], "assets": [{"name": record["feed"]["filename"],
                "url": record["feed"]["url"], "size": record["feed"]["size"], "state": "uploaded"}]}

    def test_native_feed_and_independent_timestamps(self):
        path, record = self.prepare()
        feed = record["feed"]
        self.assertEqual(feed["datetime"], 1791540240)
        self.assertEqual(record["build"]["ota_timestamp"], 1791540000)
        self.assertEqual(feed["version"], "thirteen_plus")
        self.assertEqual(feed["id"], hashlib.sha256(self.ota.read_bytes()).hexdigest())
        self.assertEqual(feed["filehash"], hashlib.md5(self.ota.read_bytes()).hexdigest())
        self.assertEqual(feed["size"], self.ota.stat().st_size)
        self.assertEqual(path.name, feed["id"] + ".json")
        for name in ("donate_url", "forum_url", "website_url", "news_url"):
            self.assertIsNone(feed[name])
        self.assertEqual(feed["maintainers"], [])
        # Preparation may retain history but must not expose an unuploaded package.
        self.assertFalse((self.site / FEED_PATH).exists())

    def test_rejects_mismatched_release_pair(self):
        for name, value in {"post-build": self.fingerprint.replace("1102", "1101"),
                            "post-build-incremental": "2026101101",
                            "post-timestamp": "1791540001",
                            "pre-device": "cepheus"}.items():
            with self.subTest(name=name):
                original = self.metadata[name]
                self.metadata[name] = value
                self.make_ota()
                with self.assertRaises(ReleaseError):
                    self.prepare()
                self.metadata[name] = original

    def test_rejects_incremental_or_wiping_ota(self):
        for name, value in (("pre-build", self.fingerprint),
                            ("pre-build-incremental", "2026101101"), ("ota-wipe", "yes")):
            with self.subTest(name=name):
                self.metadata[name] = value
                self.make_ota()
                with self.assertRaises(ReleaseError):
                    self.prepare()
                del self.metadata[name]

    def test_rejects_development_profile(self):
        for name, value in (("ro.build.type", "userdebug"), ("ro.adb.secure", "0"),
                            ("ro.debuggable", "1"), ("org.pixelexperience.version", "13_plus")):
            with self.subTest(name=name):
                original = self.props[name]
                self.props[name] = value
                self.make_target_files()
                with self.assertRaises(ReleaseError):
                    self.prepare()
                self.props[name] = original

    def test_preserves_public_v2_storage_contract(self):
        good = ("/dev/block/by-name/metadata /metadata ext4 defaults wait,first_stage_mount\n"
                "/dev/block/bootdevice/by-name/userdata /data ext4 defaults wait,fileencryption=ice::v2,keydirectory=/metadata/vold/metadata_encryption\n")
        for fstab in (good.replace("ice::v2", "ice::v1"),
                      good.replace("/by-name/userdata", "/by-name/peuserdata"),
                      good.replace("/data ext4", "/data f2fs"),
                      good.replace(",keydirectory=/metadata/vold/metadata_encryption", ""),
                      good.replace("ice::v2", "ice::v1") + "# old example fileencryption=ice::v2\n",
                      good.replace("ice::v2", "ice::v2,fileencryption=ice::v1")):
            with self.subTest(fstab=fstab):
                self.make_target_files(fstab=fstab)
                with self.assertRaises(ReleaseError):
                    self.prepare()

    def test_rejects_ab_product_and_unsigned_envelope(self):
        self.make_target_files(misc={"ab_update": "true"})
        with self.assertRaises(ReleaseError):
            self.prepare()
        self.make_target_files()
        self.make_ota(signed=False)
        with self.assertRaises(ReleaseError):
            self.prepare()

    def test_history_replay_and_immutable_identity(self):
        path, record = self.prepare()
        self.assertEqual(self.prepare(), (path, record))
        with self.assertRaises(ReleaseError):
            prepare(self.target_files, self.ota, self.site, "different-tag", "Crux-PE13-R4.zip")

    def test_upload_receipt_is_required_before_recommendation(self):
        _, record = self.prepare()
        good = self.uploaded(record)
        for bad in ({"tagName": record["tag"], "assets": []},
                    {"tagName": "wrong-tag", "assets": good["assets"]},
                    {"tagName": record["tag"], "assets": [dict(good["assets"][0], size=1)]},
                    {"tagName": record["tag"], "assets": [dict(good["assets"][0], state="new")]},
                    {"tagName": record["tag"], "assets": [dict(good["assets"][0], digest="sha256:" + "f" * 64)]},
                    {"tagName": record["tag"], "assets": [dict(good["assets"][0], url="https://example.com/wrong.zip")]}):
            with self.subTest(receipt=bad):
                with self.assertRaises(ReleaseError):
                    recommend(self.site, record["release_id"], bad)
        path = recommend(self.site, record["release_id"], good)
        self.assertEqual(json.loads(path.read_text()), record["feed"])
        self.assertEqual(validate_site(self.site), {"releases": 1, "recommended": True})
        for digest in (None, "sha256:" + record["ota"]["sha256"]):
            receipt = {"tagName": record["tag"], "assets": [dict(good["assets"][0], digest=digest)]}
            self.assertEqual(recommend(self.site, record["release_id"], receipt), path)

    def test_switches_recommendation_while_retaining_history(self):
        _, first = self.prepare()
        recommend(self.site, first["release_id"], self.uploaded(first))
        self.props["ro.build.version.incremental"] = "2026101103"
        self.props["ro.build.fingerprint"] = self.fingerprint.replace("1102", "1103")
        self.props["org.pixelexperience.build_date_utc"] = "1791540300"
        self.metadata["post-build"] = self.props["ro.build.fingerprint"]
        self.metadata["post-build-incremental"] = "2026101103"
        self.make_target_files()
        self.make_ota()
        _, second = prepare(self.target_files, self.ota, self.site, "r5-2026101103", "Crux-PE13-R5.zip")
        recommend(self.site, second["release_id"], self.uploaded(second))
        self.assertEqual(validate_site(self.site), {"releases": 2, "recommended": True})
        recommend(self.site, first["release_id"], self.uploaded(first))
        self.assertEqual(json.loads((self.site / FEED_PATH).read_text())["id"], first["release_id"])

    def test_native_schema_rejects_parser_incompatible_fields(self):
        _, record = self.prepare()
        for name, value in (("datetime", True), ("size", "100"), ("version", "13_plus"),
                            ("filehash", "A" * 32), ("id", "f" * 32), ("url", "http://example.com/ota.zip")):
            with self.subTest(name=name):
                feed = dict(record["feed"], **{name: value})
                with self.assertRaises(ReleaseError):
                    validate_feed(feed)
        feed = dict(record["feed"])
        del feed["news_url"]
        with self.assertRaises(ReleaseError):
            validate_feed(feed)

    def test_site_rejects_unpaired_recommendation_and_private_artifacts(self):
        _, record = self.prepare()
        recommend(self.site, record["release_id"], self.uploaded(record))
        write_json(self.site / FEED_PATH, dict(record["feed"], size=1))
        with self.assertRaises(ReleaseError):
            validate_site(self.site)
        write_json(self.site / FEED_PATH, record["feed"])
        for name in ("ota.zip", "releasekey.pk8"):
            with self.subTest(name=name):
                path = self.site / name
                path.write_bytes(b"must-not-be-published-by-Pages")
                with self.assertRaises(ReleaseError):
                    validate_site(self.site)
                path.unlink()

    def test_prepared_site_can_exist_before_first_upload(self):
        self.assertEqual(validate_site(self.site), {"releases": 0, "recommended": False})
        self.prepare()
        self.assertEqual(validate_site(self.site), {"releases": 1, "recommended": False})


if __name__ == "__main__":
    unittest.main()
