"""Verify anonymous, commit-pinned source access without publishing anything."""

# APMine companion change, 2026-10-15: require reviewed vendored-module notices.
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .inventory import sha256
from .bootstrap import CORE_BINARIES, RUNTIME_LIBRARIES
from .source_repository import relative_path, verify_source_snapshot
from .vendor_notices import validate as validate_vendor_notices
from .external_test_inputs import validate as validate_external_tests


MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20000
PUBLIC_HOSTS = {"api.github.com", "codeload.github.com"}


def archive_url(repository: str, commit: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repository) or repository.split("/")[1] in {".", ".."}:
        raise ValueError("Use a GitHub owner/repository name without credentials, paths or query parameters")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Pin the complete immutable GitHub commit SHA, not a branch, tag or shortened revision")
    return f"https://api.github.com/repos/{repository}/zipball/{commit}"


def require_public_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in PUBLIC_HOSTS or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.query or parsed.fragment):
        raise ValueError("Anonymous source access must stay on the approved HTTPS GitHub archive hosts")


class PublicArchiveRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        require_public_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


def download_public_archive(url: str) -> bytes:
    require_public_url(url)
    # No connector credentials, cookies, user proxies or authentication handlers
    # participate in this independent public-access check.
    opener = urllib.request.build_opener(PublicArchiveRedirect(), urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
        "User-Agent": "APMine-source-access-audit", "X-GitHub-Api-Version": "2026-03-10"})
    try:
        with opener.open(request, timeout=45) as response:
            require_public_url(response.geturl())
            if response.status != 200:
                raise ValueError("Anonymous source archive did not return a successful response")
            raw = response.read(MAX_ARCHIVE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise ValueError(f"Anonymous source archive is unavailable (HTTP {error.code}); no delivery proof exists") from error
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Public source archive exceeds the supported download bound")
    return raw


def verify_archive(raw: bytes, snapshot_raw: bytes, snapshot: dict) -> dict:
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("Public source archive exceeds the supported download bound")
    expected = {entry["path"]: entry["sha256"] for entry in snapshot["copied_inputs"]}
    if len(expected) != len(snapshot["copied_inputs"]):
        raise ValueError("Duplicate preferred-source inventory entries")
    expected["SOURCE_SNAPSHOT.json"] = sha256(snapshot_raw)
    found = set()
    roots = set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("Public source archive exceeds the supported expansion bound")
            for entry in entries:
                parts = entry.filename.rstrip("/").split("/")
                if len(parts) < 1 or any(not part for part in parts):
                    raise ValueError("Malformed public source archive path")
                relative_path(entry.filename.rstrip("/"))
                roots.add(parts[0])
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if entry.flag_bits & 1 or mode not in ({0, stat.S_IFDIR} if entry.is_dir() else {0, stat.S_IFREG}):
                    raise ValueError("Encrypted or non-regular public source archive input")
                if entry.is_dir():
                    continue
                if len(parts) < 2:
                    raise ValueError("Public archive must have one repository-root prefix")
                name = "/".join(parts[1:])
                if name not in expected or name in found:
                    raise ValueError(f"Unreviewed or duplicate public source archive input: {name}")
                found.add(name)
                if sha256(archive.read(entry)) != expected[name]:
                    raise ValueError(f"Public source differs from the reviewed build snapshot: {name}")
    except zipfile.BadZipFile as error:
        raise ValueError("Anonymous source response is not a valid repository ZIP archive") from error
    if len(roots) != 1 or found != set(expected):
        raise ValueError("Public source archive has multiple roots or missing preferred-source inputs")
    return {"archive_sha256": sha256(raw), "preferred_source_inputs": len(snapshot["copied_inputs"])}


def collect(source: Path, source_link: Path, repository: str, commit: str) -> dict:
    url = archive_url(repository, commit)
    snapshot = verify_source_snapshot(source)
    validate_external_tests(source)
    validate_vendor_notices(source)
    snapshot_raw = (source / "SOURCE_SNAPSHOT.json").read_bytes()
    source_link_raw = source_link.read_bytes()
    link = json.loads(source_link_raw)
    if (link.get("status") != "private-binary-source-link" or type(link.get("schema_version")) is not int
            or link.get("schema_version") != 1
            or link.get("gameplay_diagnostics", False) is not False
            or link.get("source_snapshot_sha256") != sha256(snapshot_raw)):
        raise ValueError("Normal binary/source record does not match this reviewed preferred-source snapshot")
    binaries = link.get("binaries", [])
    names = set()
    for binary in binaries:
        name = binary.get("name", "")
        if (name.lower() in names or name.lower() not in CORE_BINARIES | RUNTIME_LIBRARIES
                or not re.fullmatch(r"[0-9a-f]{64}", binary.get("sha256", ""))):
            raise ValueError("Binary/source access record has an unreviewed or duplicate runtime input")
        names.add(name.lower())
    if names != CORE_BINARIES | RUNTIME_LIBRARIES:
        raise ValueError("The public-source check needs the complete thirteen-binary runtime record")
    if snapshot.get("required_dependency_sources_present") is not True:
        raise ValueError("URL references alone are not the complete preferred dependency source")
    proof = verify_archive(download_public_archive(url), snapshot_raw, snapshot)
    if (source / "SOURCE_SNAPSHOT.json").read_bytes() != snapshot_raw or source_link.read_bytes() != source_link_raw:
        raise ValueError("Reviewed source/link records changed during the public-access check")
    if verify_source_snapshot(source) != snapshot:
        raise ValueError("Preferred source changed during the public-access check")
    return {"schema_version": 1, "status": "verified-anonymous-immutable-source-access", "release_ready": False,
            "source_snapshot_sha256": sha256(snapshot_raw), "binary_source_link_sha256": sha256(source_link_raw),
            "repository": repository, "immutable_revision": commit,
            "url": f"https://github.com/{repository}/tree/{commit}", "archive_url": url,
            "verified_at_utc": datetime.now(timezone.utc).isoformat(), **proof,
            "limitations": ["Public access observed at verification time, not a guarantee of continued availability",
                            "Preferred source matches the supplied reviewed binary/source record; not cryptographic build attestation",
                            "No publication, binary packaging, rights-holder permission or GPL cure/reinstatement is performed",
                            "Historical recipients need their actual release's corresponding source, not merely this new candidate"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "source-link", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Existing public-source access records are never replaced")
    if args.output.resolve().is_relative_to(args.source.resolve()):
        raise ValueError("Keep the generated access record outside immutable preferred-source inputs")
    report = collect(args.source.resolve(), args.source_link.resolve(), args.repository, args.commit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(f"Verified anonymous source access for {report['preferred_source_inputs']} matching inputs.")
    print("Read-only evidence; no source or binary release was published or declared cleared.")


if __name__ == "__main__":
    main()
