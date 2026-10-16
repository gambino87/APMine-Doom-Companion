"""Preserve the version-specific notice for the reviewed vendored Python module."""
# APMine companion change, 2026-10-15: retain the original websockets 8.1 notice.

from pathlib import Path

from .inventory import sha256


PROTOCOL_PATH = "APCpp/IXWebSocket/test/compatibility/python/websockets/vendor/protocol.py"
NOTICE_PATH = PROTOCOL_PATH.rsplit("/", 1)[0] + "/LICENSE"
TEMPLATE_PATH = "source_templates/notices/websockets-8.1.LICENSE.txt"
UPSTREAM_REVISION = "139085fe2624192a5a6c72b1e5db211dcec6ced1"
PROTOCOL_LF_SHA256 = "a2a065a2a178e668ffd77bce21dfc24baa1a49f7c45ad525f15fefff460777be"
NOTICE_SHA256 = "8a88960c0d6aa8b38faa6ee614597dbf18e0ea22ba20884ef3dd31d34b2a6d09"
MAX_BYTES = 128 * 1024


def read_input(root: Path, name: str) -> bytes:
    path = root
    for part in name.split("/"):
        path = path / part
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Vendor notice inputs cannot follow links")
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f"Missing or oversized vendor notice input: {name}")
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES or b"\0" in raw:
        raise ValueError("Invalid vendor notice text input")
    raw.decode("utf-8")
    return raw


def collect(root: Path, *, require_installed: bool = False) -> tuple[dict[str, bytes], dict | None]:
    module = root / PROTOCOL_PATH
    if not module.exists() and not module.is_symlink():
        return {}, None
    raw = read_input(root, PROTOCOL_PATH)
    normalized = raw.replace(b"\r\n", b"\n")
    if sha256(normalized) != PROTOCOL_LF_SHA256:
        raise ValueError("Vendored websockets protocol differs from the reviewed version")
    notice = read_input(Path(__file__).resolve().parent, TEMPLATE_PATH)
    if sha256(notice) != NOTICE_SHA256:
        raise ValueError("Original websockets 8.1 notice template changed")
    installed = root / NOTICE_PATH
    if require_installed or installed.exists() or installed.is_symlink():
        if read_input(root, NOTICE_PATH) != notice:
            raise ValueError("Original websockets 8.1 notice differs")
    profile = {"schema_version": 1, "component": "websockets", "version": "8.1",
               "upstream_revision": UPSTREAM_REVISION, "source_path": PROTOCOL_PATH,
               "source_sha256": sha256(raw), "source_lf_sha256": PROTOCOL_LF_SHA256,
               "notice_path": NOTICE_PATH, "notice_sha256": NOTICE_SHA256,
               "notice_url": f"https://raw.githubusercontent.com/python-websockets/websockets/{UPSTREAM_REVISION}/LICENSE",
               "scope": "Original Python websockets notice; separate from IXWebSocket's own BSD notice",
               "historical_source_delivery_complete": False, "overall_rights_cleared": False}
    return {NOTICE_PATH: notice}, profile


def validate(root: Path) -> dict | None:
    """Publication/binary-source gates require the actual installed notice."""
    return collect(root, require_installed=True)[1]
