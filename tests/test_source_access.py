import io
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import urllib.error
import zipfile

from tools.doom_companion import source_access
from tools.doom_companion.inventory import sha256


class DoomCompanionPublicSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.payload = b"/* synthetic preferred source, not a real release */\n"
        self.snapshot = {"required_dependency_sources_present": True,
                         "copied_inputs": [{"path": "main.c", "sha256": sha256(self.payload)}]}
        self.snapshot_raw = json.dumps(self.snapshot).encode()
        (self.source / "SOURCE_SNAPSHOT.json").write_bytes(self.snapshot_raw)
        self.link = self.root / "SOURCE_LINK.json"
        self.record = {"status": "private-binary-source-link", "schema_version": 1,
                       "source_snapshot_sha256": sha256(self.snapshot_raw), "gameplay_diagnostics": False,
                       "binaries": [{"name": name, "sha256": "b" * 64} for name in
                                    sorted(source_access.CORE_BINARIES | source_access.RUNTIME_LIBRARIES)]}
        self.link.write_text(json.dumps(self.record))
        self.commit = "a" * 40
        self.repository = "example/source-fixture"

    def archive(self, payloads=None):
        stream = io.BytesIO()
        if payloads is None:
            payloads = {"prefix/main.c": self.payload, "prefix/SOURCE_SNAPSHOT.json": self.snapshot_raw}
        with zipfile.ZipFile(stream, "w") as archive:
            for name, raw in payloads.items():
                archive.writestr(name, raw)
        return stream.getvalue()

    def collect(self, archive=None):
        with patch.object(source_access, "verify_source_snapshot", return_value=self.snapshot), patch.object(
                source_access, "download_public_archive", return_value=self.archive() if archive is None else archive):
            return source_access.collect(self.source, self.link, self.repository, self.commit)

    def test_exact_archive_matches_record_without_release_clearance_claim(self):
        report = self.collect()
        self.assertEqual("verified-anonymous-immutable-source-access", report["status"])
        self.assertFalse(report["release_ready"])
        self.assertEqual(self.commit, report["immutable_revision"])
        self.assertEqual(sha256(self.snapshot_raw), report["source_snapshot_sha256"])
        self.assertEqual(sha256(self.link.read_bytes()), report["binary_source_link_sha256"])
        self.assertEqual(sha256(self.archive()), report["archive_sha256"])
        self.assertEqual(1, report["preferred_source_inputs"])
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertIn("Historical recipients", report["limitations"][-1])

    def test_mutable_refs_credentials_and_other_url_syntax_are_rejected(self):
        for repository, revision in (("example/source", "main"), ("example/source", "v1.0"),
                                     ("example/source", "a" * 12), ("https://github.com/example/source", self.commit),
                                     ("name:secret@example/source", self.commit), ("example/..", self.commit),
                                     ("example/source?token=secret", self.commit)):
            with self.subTest(repository=repository, revision=revision), self.assertRaises(ValueError):
                source_access.archive_url(repository, revision)

    def test_redirects_cannot_downgrade_transport_or_use_other_hosts_or_tokens(self):
        for url in ("http://codeload.github.com/example/source", "https://evil.test/source.zip",
                    "https://user:secret@api.github.com/repos/example/source",
                    "https://codeload.github.com/example/source?token=secret", "https://api.github.com:444/source"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                source_access.require_public_url(url)
        source_access.require_public_url(source_access.archive_url(self.repository, self.commit))

    def test_missing_changed_and_unreviewed_archive_inputs_fail(self):
        for payloads, message in (({"prefix/main.c": self.payload}, "missing preferred-source"),
                                 ({"prefix/main.c": b"changed", "prefix/SOURCE_SNAPSHOT.json": self.snapshot_raw}, "differs"),
                                 ({"prefix/main.c": self.payload, "prefix/SOURCE_SNAPSHOT.json": self.snapshot_raw,
                                   "prefix/engine.exe": b"unreviewed native payload"}, "Unreviewed")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.collect(self.archive(payloads))

    def test_unsafe_paths_multiple_roots_and_duplicate_members_fail(self):
        for name in ("prefix/../main.c", "prefix//main.c", "/prefix/main.c", "prefix/C:/main.c"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.collect(self.archive({name: self.payload, "prefix/SOURCE_SNAPSHOT.json": self.snapshot_raw}))
        with self.assertRaisesRegex(ValueError, "multiple roots"):
            self.collect(self.archive({"first/main.c": self.payload, "second/SOURCE_SNAPSHOT.json": self.snapshot_raw}))
        stream = io.BytesIO(self.archive())
        with zipfile.ZipFile(stream, "a") as archive, self.assertWarns(UserWarning):
            archive.writestr("prefix/main.c", self.payload)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.collect(stream.getvalue())

    def test_symlinks_and_directory_type_disguised_as_source_fail(self):
        for mode in (stat.S_IFLNK, stat.S_IFDIR):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                member = zipfile.ZipInfo("prefix/main.c")
                member.create_system = 3
                member.external_attr = mode << 16
                archive.writestr(member, self.payload)
                archive.writestr("prefix/SOURCE_SNAPSHOT.json", self.snapshot_raw)
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "non-regular"):
                self.collect(stream.getvalue())

    def test_unbounded_or_non_archive_responses_fail(self):
        with self.assertRaisesRegex(ValueError, "ZIP archive"):
            self.collect(b"<html>Sign in required</html>")
        with patch.object(source_access, "MAX_ARCHIVE_BYTES", 4), self.assertRaisesRegex(ValueError, "download bound"):
            self.collect(self.archive())
        with patch.object(source_access, "MAX_EXPANDED_BYTES", 4), self.assertRaisesRegex(ValueError, "expansion bound"):
            self.collect()

    def test_other_snapshot_diagnostic_incomplete_and_malformed_links_fail_before_download(self):
        original = dict(self.record)
        for updates in ({"source_snapshot_sha256": "wrong"}, {"gameplay_diagnostics": True},
                        {"schema_version": True}, {"binaries": []}):
            with self.subTest(updates=updates):
                self.record.update(updates)
                self.link.write_text(json.dumps(self.record))
                with patch.object(source_access, "verify_source_snapshot", return_value=self.snapshot), patch.object(
                        source_access, "download_public_archive") as download, self.assertRaises(ValueError):
                    source_access.collect(self.source, self.link, self.repository, self.commit)
                download.assert_not_called()
                self.record = dict(original)

    def test_source_records_changed_during_download_are_rejected(self):
        def changed(url):
            self.link.write_text("changed during request")
            return self.archive()
        with patch.object(source_access, "verify_source_snapshot", return_value=self.snapshot), patch.object(
                source_access, "download_public_archive", side_effect=changed), self.assertRaisesRegex(ValueError, "records changed"):
            source_access.collect(self.source, self.link, self.repository, self.commit)

    def test_anonymous_http_failure_does_not_claim_repository_visibility_or_delivery(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://api.github.com/example", 404, "Not Found", {}, None)
        with patch.object(source_access.urllib.request, "build_opener", return_value=opener), self.assertRaisesRegex(
                ValueError, "HTTP 404.*no delivery proof"):
            source_access.download_public_archive(source_access.archive_url(self.repository, self.commit))
        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Cookie", request.headers)


if __name__ == "__main__":
    unittest.main()
