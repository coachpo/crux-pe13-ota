import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish_release
from prepare_release import prepare
from recommend_release import recommend
from release_common import FEED_PATH, ReleaseError, load_json
from publish_release import GitHub, publish, stage


class GitHubSimulation:
    """Model gh at the publication boundary, including persisted partial uploads."""

    def __init__(self, plan, pointer, previous):
        self.plan = plan
        self.pointer = pointer
        self.previous = previous
        self.current = None
        self.calls = []
        self.contents = {}
        self.fail_upload = None
        self.corrupt_final_asset = None

    def asset(self, expected):
        return {"name": expected["name"], "size": expected["bytes"],
                "browser_download_url": expected["url"], "state": "uploaded",
                "digest": "sha256:" + expected["sha256"]}

    def existing(self, assets):
        self.current = {"tag_name": self.plan["tag"], "prerelease": True, "draft": False,
                        "html_url": "https://github.com/coachpo/crux-pe13-ota/releases/tag/" + self.plan["tag"],
                        "body": self.plan["notes_body"],
                        "assets": assets}

    def __call__(self, arguments, **kwargs):
        if self.pointer.read_bytes() != self.previous:
            raise AssertionError("The feed changed before remote verification finished")
        self.calls.append(arguments)
        if arguments[1] == "api":
            if self.current is None:
                return subprocess.CompletedProcess(arguments, 1, "", "gh: Not Found (HTTP 404)")
            current = json.loads(json.dumps(self.current))
            if self.corrupt_final_asset and len(current["assets"]) == len(self.plan["assets"]):
                current["assets"][0]["digest"] = "sha256:" + "f" * 64
            return subprocess.CompletedProcess(arguments, 0, json.dumps(current), "")
        operation = arguments[2]
        if operation == "create":
            self.existing([])
            notes = Path(arguments[arguments.index("--notes-file") + 1])
            self.current["body"] = notes.read_bytes().decode("utf-8")
        elif operation == "upload":
            path = Path(arguments[-1])
            if path.name == self.fail_upload:
                self.fail_upload = None
                return subprocess.CompletedProcess(arguments, 1, "", "Upload connection interrupted")
            if any(asset["name"] == path.name for asset in self.current["assets"]):
                raise AssertionError("Publisher attempted to replace an existing asset")
            expected = next(asset for asset in self.plan["assets"] if asset["name"] == path.name)
            self.current["assets"].append(self.asset(expected))
            self.contents[path.name] = path
        elif operation == "download":
            name = arguments[arguments.index("--pattern") + 1]
            directory = Path(arguments[arguments.index("--dir") + 1])
            shutil.copyfile(self.contents[name], directory / name)
        else:
            raise AssertionError("Unexpected gh operation: " + str(arguments))
        return subprocess.CompletedProcess(arguments, 0, "", "")


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.site = self.root / "site"
        self.site.mkdir()
        (self.site / "index.html").write_text("<!doctype html><title>Crux</title>")
        self.target_files = self.root / "signed-target_files.zip"
        self.ota = self.root / "ota.zip"
        self.recovery = self.root / "Crux-Recovery.img"
        self.recovery.write_bytes(b"same-release-recovery")
        self.notes = self.root / "release-notes.md"
        self.notes.write_text("Crux public OTA smoke candidate.\n")
        self.guide = self.root / "INSTALL.md"
        self.guide.write_text("Install through the system updater.\n")
        self.evidence = self.root / "evidence"
        self.make_pair("2026101103", "1791702948")
        _, previous = prepare(self.target_files, self.ota, self.site, "r5-2026101103", "R5.zip")
        recommend(self.site, previous["release_id"], {
            "tagName": previous["tag"], "assets": [{"name": previous["feed"]["filename"],
                "url": previous["feed"]["url"], "size": previous["feed"]["size"]}]})
        self.pointer = self.site / FEED_PATH
        self.previous = self.pointer.read_bytes()
        self.make_pair("2026101201", "1791790000")

    def make_pair(self, incremental, timestamp):
        fingerprint = "Xiaomi/crux/crux:13/TQ3A.230901.001.B1/" + incremental + ":user/release-keys"
        props = {"org.pixelexperience.device": "crux", "ro.product.system.device": "crux",
                 "ro.build.type": "user", "ro.secure": "1", "ro.debuggable": "0",
                 "ro.adb.secure": "1", "org.pixelexperience.version": "thirteen_plus",
                 "ro.build.version.release": "13", "ro.build.version.incremental": incremental,
                 "ro.build.fingerprint": fingerprint, "ro.build.date.utc": timestamp,
                 "org.pixelexperience.build_date_utc": timestamp}
        metadata = {"ota-type": "BLOCK", "pre-device": "crux", "post-build": fingerprint,
                    "post-build-incremental": incremental, "post-timestamp": timestamp}
        text = lambda values: "".join(key + "=" + value + "\n" for key, value in values.items())
        with zipfile.ZipFile(self.target_files, "w") as archive:
            archive.writestr("SYSTEM/build.prop", text(props))
            archive.writestr("META/misc_info.txt", "")
            archive.writestr("RECOVERY/RAMDISK/system/etc/recovery.fstab",
                "/dev/block/by-name/metadata /metadata ext4 defaults wait\n"
                "/dev/block/by-name/userdata /data ext4 defaults wait,fileencryption=ice::v2,keydirectory=/metadata/vold/metadata_encryption\n")
            for partition in ("boot", "recovery", "system", "vendor", "dtbo", "vbmeta"):
                archive.writestr("IMAGES/" + partition + ".img",
                    self.recovery.read_bytes() if partition == "recovery" else b"fixture-image")
        with zipfile.ZipFile(self.ota, "w") as archive:
            archive.writestr("META-INF/com/android/metadata", text(metadata))
            archive.writestr("META-INF/com/android/otacert", b"fixture-public-certificate")
            signature = b"fixture-whole-file-signature"
            size = len(signature) + 6
            archive.comment = signature + size.to_bytes(2, "little") + b"\xff\xff" + size.to_bytes(2, "little")

    def stage(self, **kwargs):
        return stage(self.target_files, self.ota, self.recovery, self.notes,
                     self.guide, kwargs.pop("extras", []), self.site, self.evidence,
                     kwargs.pop("tag", "r6-2026101201"), "Crux-R6-OTA.zip", **kwargs)

    def simulation(self, plan):
        return GitHubSimulation(plan, self.pointer, self.previous)

    def test_prepare_stages_public_bytes_without_recommending_or_calling_github(self):
        with patch("publish_release.subprocess.run", side_effect=AssertionError("Unexpected remote command")):
            plan = self.stage()
        self.assertEqual(self.pointer.read_bytes(), self.previous)
        self.assertEqual({asset["name"] for asset in plan["assets"]},
                         {"Crux-R6-OTA.zip", self.recovery.name, self.notes.name, self.guide.name})
        self.assertEqual((self.evidence / "assets/Crux-R6-OTA.zip").read_bytes(), self.ota.read_bytes())
        self.assertEqual(load_json(self.evidence / "publication-plan.json"), plan)

    def test_recovery_mismatch_stops_preparation(self):
        self.recovery.write_bytes(self.recovery.read_bytes()[:-1] + b"x")
        with self.assertRaisesRegex(ReleaseError, "Recovery differs"):
            self.stage()
        self.assertEqual(self.pointer.read_bytes(), self.previous)
        self.assertFalse(self.evidence.exists())
        self.assertEqual(len(list((self.site / "releases").glob("*.json"))), 1)

    def test_private_files_cannot_be_staged_as_extra_public_assets(self):
        key = self.root / "releasekey.pk8"
        key.write_bytes(b"private-key-bytes")
        pem = self.root / "signing.pem"
        pem.write_text("-----BEGIN PRIVATE KEY-----\nsecret\n")
        renamed_target_files = self.root / "renamed.zip"
        shutil.copyfile(self.target_files, renamed_target_files)
        for private in (key, pem, renamed_target_files):
            with self.subTest(private=private), self.assertRaises(ReleaseError):
                self.stage(extras=[private])
        self.assertFalse(self.evidence.exists())
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_retained_tag_cannot_be_relabelled(self):
        with self.assertRaisesRegex(ReleaseError, "immutable tag"):
            self.stage(tag="r5-2026101103")
        self.assertEqual(len(list((self.site / "releases").glob("*.json"))), 1)
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_new_prerelease_verifies_every_uploaded_asset_before_recommendation(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        with patch("publish_release.recommend", wraps=recommend) as recommended:
            receipt = publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(recommended.call_count, 1)
        self.assertEqual(receipt["status"], "published_and_recommended_locally")
        self.assertEqual(load_json(self.pointer)["id"], plan["release_id"])
        self.assertEqual(receipt["uploaded"], [asset["name"] for asset in plan["assets"]])
        self.assertEqual(simulation.calls[0][1], "api")
        self.assertEqual(simulation.calls[1][2], "create")
        self.assertEqual(simulation.calls[-1][1], "api")
        self.assertTrue(all("--clobber" not in call for call in simulation.calls))
        self.assertEqual(load_json(self.evidence / "publication-receipt.json"), receipt)

    def test_failed_upload_keeps_feed_and_retry_only_uploads_missing_files(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        simulation.fail_upload = self.notes.name
        with self.assertRaisesRegex(ReleaseError, "interrupted"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(self.pointer.read_bytes(), self.previous)
        failed = load_json(self.evidence / "publication-receipt.json")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["uploaded"], ["Crux-R6-OTA.zip", self.recovery.name])
        simulation.calls.clear()
        receipt = publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(receipt["reused"], ["Crux-R6-OTA.zip", self.recovery.name])
        self.assertEqual(receipt["uploaded"], [self.notes.name, self.guide.name])
        self.assertFalse(any(call[1:3] == ["release", "create"] for call in simulation.calls))

    def test_wrong_same_name_asset_stops_before_any_upload_or_recommendation(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        wrong = simulation.asset(plan["assets"][0])
        wrong["digest"] = "sha256:" + "f" * 64
        simulation.existing([wrong])
        with self.assertRaisesRegex(ReleaseError, "digest differs"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(self.pointer.read_bytes(), self.previous)
        self.assertEqual(len(simulation.calls), 1)
        self.assertEqual(simulation.current["assets"], [wrong])

    def test_existing_draft_or_non_candidate_release_is_never_modified(self):
        plan = self.stage()
        for changed in ({"draft": True}, {"prerelease": False}, {"tag_name": "another-tag"}):
            with self.subTest(changed=changed):
                simulation = self.simulation(plan)
                simulation.existing([])
                simulation.current.update(changed)
                with self.assertRaisesRegex(ReleaseError, "Resume only"):
                    publish(plan, self.site, self.evidence, GitHub(simulation))
                self.assertEqual(len(simulation.calls), 1)
                self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_remote_tag_with_another_rom_filename_is_immutable_without_local_history(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        different_rom = simulation.asset(plan["assets"][0])
        different_rom["name"] = "Another-PE13-OTA.zip"
        simulation.existing([different_rom])
        original = json.loads(json.dumps(simulation.current))
        with self.assertRaisesRegex(ReleaseError, "assets outside"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(len(simulation.calls), 1)
        self.assertEqual(simulation.current, original)
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_remote_release_notes_must_equal_the_frozen_publication_notes(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        simulation.existing([])
        simulation.current["body"] = "Notes for an older candidate.\n"
        original = json.loads(json.dumps(simulation.current))
        with self.assertRaisesRegex(ReleaseError, "different release notes"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(len(simulation.calls), 1)
        self.assertEqual(simulation.current, original)
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_lookup_error_does_not_create_or_upload_a_release(self):
        plan = self.stage()
        calls = []
        def unavailable(arguments, **kwargs):
            calls.append(arguments)
            return subprocess.CompletedProcess(arguments, 1, "", "gh: Forbidden (HTTP 403)")
        with self.assertRaisesRegex(ReleaseError, "Forbidden"):
            publish(plan, self.site, self.evidence, GitHub(unavailable))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_missing_digest_is_verified_by_downloading_actual_bytes(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        assets = [simulation.asset(asset) for asset in plan["assets"]]
        for asset in assets:
            asset["digest"] = None
        simulation.existing(assets)
        simulation.contents = {asset["name"]: Path(asset["path"]) for asset in plan["assets"]}
        receipt = publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(receipt["uploaded"], [])
        self.assertEqual(receipt["reused"], [asset["name"] for asset in plan["assets"]])
        self.assertEqual(sum(call[1:3] == ["release", "download"] for call in simulation.calls), 4)

    def test_missing_digest_download_mismatch_stops_before_upload(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        asset = simulation.asset(plan["assets"][0])
        asset["digest"] = None
        simulation.existing([asset])
        wrong = self.root / "wrong.zip"
        wrong.write_bytes(b"x" * plan["assets"][0]["bytes"])
        simulation.contents[asset["name"]] = wrong
        with self.assertRaisesRegex(ReleaseError, "Downloaded asset differs"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(self.pointer.read_bytes(), self.previous)
        self.assertFalse(any(call[1:3] == ["release", "upload"] for call in simulation.calls))

    def test_final_remote_identity_failure_keeps_prior_feed(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        simulation.corrupt_final_asset = True
        with self.assertRaisesRegex(ReleaseError, "digest differs"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_failed_site_validation_restores_prior_recommendation(self):
        plan = self.stage()
        simulation = self.simulation(plan)
        original = publish_release.validate_site
        def validate(site):
            if load_json(self.pointer)["id"] == plan["release_id"]:
                raise ReleaseError("Site validation failed")
            return original(site)
        with patch("publish_release.validate_site", side_effect=validate):
            with self.assertRaisesRegex(ReleaseError, "Site validation failed"):
                publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(self.pointer.read_bytes(), self.previous)

    def test_changed_staged_asset_is_rejected_before_remote_work(self):
        plan = self.stage()
        Path(plan["assets"][0]["path"]).write_bytes(b"changed")
        simulation = self.simulation(plan)
        with self.assertRaisesRegex(ReleaseError, "Staged asset changed"):
            publish(plan, self.site, self.evidence, GitHub(simulation))
        self.assertEqual(simulation.calls, [])
        self.assertEqual(self.pointer.read_bytes(), self.previous)


if __name__ == "__main__":
    unittest.main()
