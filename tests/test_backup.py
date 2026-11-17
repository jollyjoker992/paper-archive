"""Backup tests: made-up documents only. The upload tests run the real rclone against a local folder and are
skipped when rclone is not installed. Run: python3 -m unittest discover -s tests -v"""
import contextlib
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from paperarchive import backup, cli, store  # noqa: E402
import test_paper_archive as base  # noqa: E402

KEY = "a" * 52
AWS = {"PAPER_AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "PAPER_AWS_SECRET_ACCESS_KEY": "aws-secret", "PAPER_AWS_BUCKET": "b/p/"}
R2 = {"PAPER_R2_ACCOUNT_ID": "0" * 32, "PAPER_R2_ACCESS_KEY_ID": "r2id", "PAPER_R2_SECRET_ACCESS_KEY": "r2-secret",
      "PAPER_R2_BUCKET": "papers"}
GDRIVE = {"PAPER_GDRIVE_TOKEN": '{"access_token":"x","refresh_token":"y"}', "PAPER_GDRIVE_CLIENT_ID": "cid",
          "PAPER_GDRIVE_CLIENT_SECRET": "drive-secret"}


def tree(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


class Configuration(unittest.TestCase):
    def test_nothing_set_means_no_target(self):
        self.assertEqual(backup.targets_from_env({}), {})

    def test_each_vendor_is_enabled_by_its_credentials(self):
        targets = backup.targets_from_env({**AWS, **R2, **GDRIVE, "PAPER_BACKUP_RCLONE_REMOTE": "mine:papers/"})
        self.assertEqual(list(targets), ["aws", "r2", "gdrive", "rclone"])
        self.assertEqual(targets["aws"].remote, "paperaws:b/p")
        self.assertEqual(targets["aws"].options["REGION"], "us-east-1")
        self.assertEqual(targets["r2"].options["ENDPOINT"], f"https://{'0' * 32}.r2.cloudflarestorage.com")
        self.assertEqual(targets["gdrive"].remote, "papergdrive:paper-archive")
        self.assertEqual(targets["rclone"].remote, "mine:papers")
        self.assertFalse(targets["rclone"].private_config)

    def test_a_half_configured_target_is_a_configuration_error(self):
        for env in ({"PAPER_AWS_BUCKET": "b"}, {**R2, "PAPER_R2_ACCOUNT_ID": "not-hex"},
                    {**AWS, "PAPER_AWS_BUCKET": "s3:b"}, {**GDRIVE, "PAPER_GDRIVE_TOKEN": "ya29.token"},
                    {**GDRIVE, "PAPER_GDRIVE_TOKEN": '{"access_token":"x"}'}):
            with self.assertRaises(store.ArchiveError, msg=env):
                backup.targets_from_env(env)
        # another target's mistake does not stop a run for one target
        self.assertEqual(list(backup.targets_from_env({**AWS, "PAPER_R2_BUCKET": "b"}, only="aws")), ["aws"])

    def test_encrypted_or_plain_must_be_chosen(self):
        self.assertEqual(backup.mode_from_env({"PAPER_BACKUP_KEY": KEY}).folder, "encrypted")
        self.assertEqual(backup.mode_from_env({"PAPER_BACKUP_PLAIN": "true"}).folder, "plain")
        for env in ({}, {"PAPER_BACKUP_PLAIN": "false"}, {"PAPER_BACKUP_KEY": KEY, "PAPER_BACKUP_PLAIN": "true"},
                    {"PAPER_BACKUP_KEY": "my dog's name"}):
            with self.assertRaises(store.ArchiveError, msg=env):
                backup.mode_from_env(env)
        self.assertRegex(backup.new_key(), backup.KEY_RE)
        self.assertNotEqual(backup.new_key(), backup.new_key())

    def test_remote_paths(self):
        self.assertEqual(backup.rjoin("papercrypt:", "manifest.json"), "papercrypt:manifest.json")
        self.assertEqual(backup.rjoin("paperaws:b/p", "plain"), "paperaws:b/p/plain")
        self.assertEqual(backup.remote_name("paperaws:b"), "paperaws")
        self.assertIsNone(backup.remote_name("/Volumes/disk/a:b"))


class WithArchive(unittest.TestCase):
    add = base.Archive.add

    def setUp(self):
        base.Archive.setUp(self)
        self.cloud = self.root.parent / (self.root.name + "-cloud")
        self.addCleanup(shutil.rmtree, self.cloud, ignore_errors=True)
        self.env = {"PAPER_BACKUP_RCLONE_REMOTE": str(self.cloud), "PAPER_BACKUP_PLAIN": "true"}
        # No rclone configuration of this computer, and no .env of this repository, reaches a test.
        conf = self.root / "rclone-test.conf"
        conf.write_text("")
        patches = [unittest.mock.patch.dict("os.environ", {"RCLONE_CONFIG": str(conf), "PAPER_ARCHIVE_DIR": str(self.root)}),
                   unittest.mock.patch.object(store, "REPO_DIR", self.root)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_backup(self, env=None, rclone=None):
        env = env or self.env
        return backup.backup(self.root, backup.targets_from_env(env), backup.mode_from_env(env),
                             rclone or backup.Rclone(self.root, env))

    def restore(self, env=None, name="restored"):
        env = env or self.env
        out = self.root.parent / f"{self.root.name}-{name}"
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        return out, backup.restore(backup.targets_from_env(env)["rclone"], backup.mode_from_env(env), out,
                                   backup.Rclone(self.root, env))

    def manifest(self, folder="plain"):
        return json.loads((self.cloud / folder / "manifest.json").read_text())


class Archives(WithArchive):
    def test_an_archive_is_reproducible_and_holds_only_what_cannot_be_made_again(self):
        doc = self.add("lab-report")
        (doc.path / "fhir.ndjson").write_text("{}\n")
        (doc.path / "notes.txt").write_text("left by someone")
        first, second = self.root / "a.tar", self.root / "b.tar"
        sha, size = backup.build_archive(doc, first)
        (doc.path / "extraction.json").touch()                              # a new time, the same bytes
        self.assertEqual(backup.build_archive(doc, second), (sha, size))
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as tar:
            names = tar.getnames()
        self.assertEqual(names, sorted(f"{doc.id}/{f}" for f in
                                       ["document.pdf", "extraction.json", "meta.json", "review.json",
                                        doc.meta["originals"][0]["file"]]))

    def test_an_archive_that_holds_anything_else_is_not_unpacked(self):
        doc = self.add("ecg")
        for name, kind in ((f"{doc.id}/../../escape", tarfile.REGTYPE), ("d_0000000000000000/meta.json", tarfile.REGTYPE),
                           (f"{doc.id}/fhir.ndjson", tarfile.REGTYPE), (f"{doc.id}/meta.json", tarfile.SYMTYPE)):
            archive = self.root / "bad.tar"
            with tarfile.open(archive, "w") as tar:
                info = tarfile.TarInfo(name)
                info.type, info.size, info.linkname = kind, 0, "/etc/passwd"
                tar.addfile(info, io.BytesIO(b""))
            with self.assertRaises(backup.BackupError, msg=name):
                backup.extract_document(archive, doc.id, self.root / "unpacked")


@unittest.skipUnless(shutil.which("rclone"), "rclone is not installed")
class BackupWithRclone(WithArchive):
    def test_uploads_every_document_and_nothing_twice(self):
        approved, taken_in = self.add("lab-report"), self.add("ecg", approve=False)
        (self.root / "fhir").mkdir()
        (self.root / "fhir" / "load-manifest.json").write_text('{"documents": {}}')
        report = self.run_backup()
        self.assertFalse(backup.failed(report))
        self.assertEqual(report["targets"]["rclone"], {"documents": 2, "backed_up": 2, "uploaded": 4, "pending": 0,
                                                       "error": ""})
        manifest = self.manifest()
        self.assertEqual({k: v["state"] for k, v in manifest["documents"].items()},
                         {approved.id: "approved", taken_in.id: "extracted"})
        self.assertEqual(sorted(manifest["extras"]), ["fhir/load-manifest.json", "people.toml"])
        files = tree(self.cloud)
        self.assertEqual(len(files), 5)
        for entry in list(manifest["documents"].values()) + list(manifest["extras"].values()):
            self.assertEqual(len(files[f"plain/{entry['file']}"]), entry["bytes"])
        self.assertEqual(self.run_backup()["targets"]["rclone"]["uploaded"], 0)
        self.assertEqual(tree(self.cloud), files)                           # not even manifest.json again
        self.assertFalse(list((self.root / "backup").glob(".staging-*")))
        self.assertEqual(len(store.documents(self.root)), 2)                # backup/ is not taken for a document

    def test_a_changed_document_gets_a_new_file_and_the_old_one_stays(self):
        doc = self.add("lab-report", approve=False)
        self.run_backup()
        before = self.manifest()["documents"][doc.id]
        store.approve(doc, "Owner", [], accept_warnings=False)
        self.assertEqual(self.run_backup()["targets"]["rclone"]["uploaded"], 1)
        after = self.manifest()["documents"][doc.id]
        self.assertNotEqual(after["file"], before["file"])
        self.assertEqual(after["state"], "approved")
        self.assertTrue((self.cloud / "plain" / before["file"]).is_file())

    def test_a_removed_document_leaves_the_manifest_but_not_the_target(self):
        keep, gone = self.add("lab-report"), self.add("ecg")
        self.run_backup()
        file = self.manifest()["documents"][gone.id]["file"]
        store.remove(gone)
        self.assertFalse(backup.failed(self.run_backup()))
        self.assertEqual(list(self.manifest()["documents"]), [keep.id])
        self.assertTrue((self.cloud / "plain" / file).is_file())

    def test_a_file_deleted_on_the_target_is_uploaded_again(self):
        doc = self.add("lab-report")
        self.run_backup()
        (self.cloud / "plain" / self.manifest()["documents"][doc.id]["file"]).unlink()
        self.assertEqual(self.run_backup()["targets"]["rclone"]["uploaded"], 1)
        self.assertTrue((self.cloud / "plain" / self.manifest()["documents"][doc.id]["file"]).is_file())

    def test_a_lost_or_damaged_state_uploads_nothing_again(self):
        self.add("lab-report")
        self.run_backup()
        files = tree(self.cloud)
        for damage in (None, "{not json", '{"targets": []}'):
            backup.state_path(self.root).unlink()
            if damage:
                backup.state_path(self.root).write_text(damage)
            report = self.run_backup()
            self.assertEqual(report["targets"]["rclone"]["uploaded"], 0, damage)
            self.assertEqual(tree(self.cloud), files, damage)

    def test_a_failed_copy_never_leaves_the_manifest_naming_a_file_the_target_lacks(self):
        doc = self.add("lab-report", approve=False)
        self.run_backup()
        first = self.manifest()["documents"][doc.id]
        store.approve(doc, "Owner", [], accept_warnings=False)

        class Failing(backup.Rclone):
            def upload(self, target, mode, src, rels):
                if rels == ["manifest.json"]:
                    return super().upload(target, mode, src, rels)
                return "rclone exited 1: simulated"

        report = self.run_backup(rclone=Failing(self.root, self.env))
        self.assertTrue(backup.failed(report))
        self.assertEqual(report["targets"]["rclone"]["pending"], 1)
        self.assertIn("simulated", report["targets"]["rclone"]["error"])
        self.assertEqual(self.manifest()["documents"][doc.id], first)       # the last version it holds
        status = backup.status(self.root, self.env)["targets"]["rclone"]
        self.assertEqual((status["backed_up"], status["up_to_date"]), (0, False))
        self.assertFalse(backup.failed(self.run_backup()))                  # the next run heals it
        self.assertNotEqual(self.manifest()["documents"][doc.id], first)
        self.assertTrue(backup.status(self.root, self.env)["targets"]["rclone"]["up_to_date"])

    def test_an_unreachable_target_is_reported_and_retried(self):
        self.add("lab-report")

        class Down(backup.Rclone):
            def listing(self, target, mode):
                raise backup.BackupError("cannot list rclone: simulated")

        report = self.run_backup(rclone=Down(self.root, self.env))
        self.assertTrue(backup.failed(report))
        self.assertIn("simulated", backup.status(self.root, self.env)["targets"]["rclone"]["error"])
        self.assertFalse(self.cloud.exists())
        self.assertFalse(backup.failed(self.run_backup()))

    def test_an_encrypted_target_shows_no_name_and_no_content(self):
        doc = self.add("lab-report")
        env = {**self.env, "PAPER_BACKUP_PLAIN": "", "PAPER_BACKUP_KEY": KEY}
        self.assertFalse(backup.failed(self.run_backup(env)))
        files = tree(self.cloud)
        self.assertEqual(len(files), 3)
        for name, content in files.items():
            self.assertTrue(name.startswith("encrypted/"), name)
            for readable in (doc.id, "manifest", "documents", "people", ".tar"):
                self.assertNotIn(readable, name)
            for marker in (b"%PDF", b"Owner", doc.id.encode(), "Trần".encode()):
                self.assertNotIn(marker, content)
        self.assertEqual(self.run_backup(env)["targets"]["rclone"]["uploaded"], 0)
        out, report = self.restore(env)
        self.assertEqual(report, {"documents": [doc.id], "extras": ["people.toml"], "errors": {}})

    def test_another_key_is_refused_and_changes_nothing(self):
        self.add("lab-report")
        env = {**self.env, "PAPER_BACKUP_PLAIN": "", "PAPER_BACKUP_KEY": KEY}
        self.run_backup(env)
        files = tree(self.cloud)
        other = {**env, "PAPER_BACKUP_KEY": "b" * 52}
        with self.assertRaisesRegex(store.ArchiveError, "not the key"):
            self.run_backup(other)
        with self.assertRaisesRegex(store.ArchiveError, "not the key"):
            self.restore(other)
        self.assertEqual(tree(self.cloud), files)

    def test_plain_and_encrypted_copies_live_side_by_side(self):
        self.add("lab-report")
        self.run_backup()
        plain = tree(self.cloud)
        env = {**self.env, "PAPER_BACKUP_PLAIN": "", "PAPER_BACKUP_KEY": KEY}
        self.assertEqual(self.run_backup(env)["targets"]["rclone"]["uploaded"], 2)
        after = tree(self.cloud)
        self.assertEqual({k: v for k, v in after.items() if k.startswith("plain/")}, plain)
        self.assertEqual(self.run_backup()["targets"]["rclone"]["uploaded"], 0)     # each keeps its own state

    def test_restore_gives_back_the_documents_with_their_approval(self):
        approved, taken_in = self.add("lab-report"), self.add("ecg", approve=False)
        (approved.path / "fhir.ndjson").write_text("{}\n")
        self.run_backup()
        out, report = self.restore()
        self.assertEqual(report, {"documents": sorted([approved.id, taken_in.id]), "extras": ["people.toml"],
                                  "errors": {}})
        expected = {k: v for k, v in tree(self.root).items()
                    if k == "people.toml" or (k.startswith("documents/") and not k.endswith("fhir.ndjson"))}
        self.assertEqual(tree(out), expected)
        self.assertEqual([d.approved() for d in store.documents(out)], [d.approved() for d in store.documents(self.root)])
        self.assertEqual(sorted(d.approved() for d in store.documents(out)), [False, True])
        with self.assertRaisesRegex(store.ArchiveError, "not an empty folder"):
            self.restore()

    def test_restore_rejects_a_changed_file_and_says_which(self):
        good, bad = self.add("lab-report"), self.add("ecg")
        self.run_backup()
        archive = self.cloud / "plain" / self.manifest()["documents"][bad.id]["file"]
        archive.write_bytes(archive.read_bytes()[::-1])                     # the same size, other bytes
        out, report = self.restore()
        self.assertEqual(report["documents"], [good.id])
        self.assertIn("sha256 differs", report["errors"][bad.id])
        self.assertEqual([p.name for p in (out / "documents").iterdir()], [good.id])

    def test_restore_takes_no_path_from_the_manifest(self):
        doc = self.add("lab-report")
        self.run_backup()
        path = self.cloud / "plain" / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["documents"]["../../outside"] = dict(manifest["documents"][doc.id])
        manifest["documents"][doc.id]["file"] = "extras/../../elsewhere.tar"
        manifest["extras"]["../people.toml"] = manifest["extras"].pop("people.toml")
        path.write_text(json.dumps(manifest))
        out, report = self.restore()
        self.assertEqual((report["documents"], report["extras"]), ([], []))
        self.assertEqual(sorted(report["errors"]), sorted(["../../outside", doc.id, "../people.toml"]))
        self.assertEqual(tree(out), {})

    def test_restore_from_an_empty_target_says_so(self):
        with self.assertRaisesRegex(backup.BackupError, "no backup found"):
            self.restore()

    def test_cli(self):
        doc = self.add("lab-report")

        def run(*argv, **env):
            out = io.StringIO()
            with unittest.mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out), \
                    contextlib.redirect_stderr(io.StringIO()):
                return cli.main(list(argv)), out.getvalue()

        self.assertEqual(run("backup")[0], 2)                                       # no target
        self.assertEqual(run("backup", **{"PAPER_BACKUP_RCLONE_REMOTE": str(self.cloud)})[0], 2)   # no mode chosen
        self.assertFalse(self.cloud.exists())
        self.assertEqual(run("backup", "--target", "aws", **self.env)[0], 2)        # not that target
        self.assertEqual(run("backup", **self.env, PAPER_RCLONE_BIN="/nonexistent/rclone")[0], 2)
        self.assertIn("never run", run("backup", "status", **self.env)[1])
        code, out = run("backup", **self.env)
        self.assertEqual((code, json.loads(out)["targets"]["rclone"]["backed_up"]), (0, 1))
        code, out = run("backup", "status", **self.env)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"backup rclone \(plain\): 1/1 document\(s\), up to date")
        self.assertTrue(json.loads(run("backup", "status", "--json", **self.env)[1])["targets"]["rclone"]["up_to_date"])
        self.assertIn("choose how", run("backup", "status")[1])
        restored = self.root.parent / f"{self.root.name}-cli"
        self.addCleanup(shutil.rmtree, restored, ignore_errors=True)
        code, out = run("restore", "--from", "rclone", "--out", str(restored), **self.env)
        self.assertEqual((code, json.loads(out)["documents"]), (0, [doc.id]))
        self.assertEqual(run("restore", "--from", "rclone", "--out", str(restored), **self.env)[0], 2)
        code, out = run("backup", "new-key")
        self.assertRegex(out, r"^PAPER_BACKUP_KEY=[a-z2-7]{52}\n$")

    def test_credentials_reach_rclone_in_its_environment_only(self):
        log = self.root / "calls.log"
        wrapper = self.root / "rclone-logged"
        wrapper.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nenv | grep RCLONE_CONFIG >> "{log}.env"\n'
                           f'exec "{shutil.which("rclone")}" "$@"\n')
        wrapper.chmod(0o755)
        self.add("lab-report")
        env = {**AWS, "PAPER_BACKUP_KEY": KEY, "PAPER_RCLONE_BIN": str(wrapper)}
        rclone = backup.Rclone(self.root, env)
        target = backup.targets_from_env(env)["aws"]
        rclone.run(target, backup.mode_from_env(env), "version")
        calls, seen = log.read_text(), Path(f"{log}.env").read_text()
        for secret in ("aws-secret", KEY):
            self.assertNotIn(secret, calls)
        self.assertIn("RCLONE_CONFIG_PAPERAWS_SECRET_ACCESS_KEY=aws-secret", seen)
        self.assertIn("RCLONE_CONFIG_PAPERAWSCRYPT_REMOTE=paperaws:b/p/encrypted", seen)
        self.assertNotIn(KEY, seen)                                         # the key is passed obscured
        self.assertIn(f"RCLONE_CONFIG={self.root / 'backup' / 'rclone.conf'}", seen)
        self.assertEqual((self.root / "backup" / "rclone.conf").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
