"""Hermetic test bootstrap (WP-00, re-artha §11.7).

Imported first by ``tests/conftest.py``.  Everything here runs before any
Artha module is imported so that module-level ``Path.home()`` /
``expanduser`` lookups, credential reads and network clients resolve to
isolated, inert values.

Guarantees
----------
* ``HOME`` (and the Windows equivalents) point at a per-session temp dir, so
  ``~/.artha-local``, ``~/.artha-tokens`` and friends are never the real ones.
* The OS credential store is replaced by the null keyring backend.
* Credential-shaped environment variables are removed.
* Non-loopback sockets and DNS lookups raise :class:`NetworkAccessError`
  unless the running test is marked ``network``.
* Production data directories inside the checkout are snapshotted so a test
  that mutates them fails the session (:class:`ProtectedSnapshot`).
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import tempfile
from pathlib import Path

REAL_HOME_ENV = "ARTHA_TEST_REAL_HOME"
NULL_KEYRING = "keyring.backends.null.Keyring"
PROTECTED_ROOTS = ("state", "briefings", "summaries", "tmp", "backups", "config")
# Files this large are compared by size + mtime only (hashing 800 MB per run is wasteful).
_HASH_LIMIT = 5 * 1024 * 1024

_CREDENTIAL_RE = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL|AGE_KEY)",
    re.IGNORECASE,
)
# Variables that look like credentials by name but are harmless configuration.
_CREDENTIAL_ALLOW = {"ARTHA_PLAID_ENVIRONMENT"}


class NetworkAccessError(socket.gaierror):
    """Raised when a hermetic test attempts a real network operation.

    A ``gaierror`` (an ``OSError``) so production code sees what an offline
    machine produces and exercises its own offline handling, rather than
    crashing on a foreign exception type.
    """

    def __init__(self, message: str):
        super().__init__(socket.EAI_NONAME, message)


def looks_like_credential_env(name: str) -> bool:
    return name not in _CREDENTIAL_ALLOW and bool(_CREDENTIAL_RE.search(name))


def is_loopback_host(host: object) -> bool:
    if host is None or host == "":
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    text = str(host).strip("[]")
    if text.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------

_installed_home: Path | None = None


def isolate_environment() -> Path:
    """Redirect HOME, local dir, keyring and credentials. Idempotent."""
    global _installed_home
    if _installed_home is not None:
        return _installed_home

    os.environ.setdefault(REAL_HOME_ENV, os.path.expanduser("~"))
    home = Path(tempfile.mkdtemp(prefix="artha-test-home-")).resolve()
    for var in ("HOME", "USERPROFILE"):
        os.environ[var] = str(home)
    os.environ["ARTHA_LOCAL_DIR"] = str(home / ".artha-local")
    os.environ["PYTHON_KEYRING_BACKEND"] = NULL_KEYRING
    os.environ["ARTHA_NO_REEXEC"] = "1"
    os.environ["ARTHA_TEST_MODE"] = "1"
    for name in [n for n in os.environ if looks_like_credential_env(n)]:
        del os.environ[name]
    _installed_home = home
    return home


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------

_network_allowed = False
_guard_installed = False


def set_network_allowed(allowed: bool) -> None:
    global _network_allowed
    _network_allowed = allowed


def _address_host(address: object) -> object:
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def install_network_guard() -> None:
    global _guard_installed
    if _guard_installed:
        return
    _guard_installed = True

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo
    real_gethostbyname = socket.gethostbyname

    def _check(host: object) -> None:
        if _network_allowed or is_loopback_host(host):
            return
        raise NetworkAccessError(f"real network access blocked in tests: {host!r}")

    def connect(self, address):  # type: ignore[no-untyped-def]
        if self.family != getattr(socket, "AF_UNIX", object()):
            _check(_address_host(address))
        return real_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        if self.family != getattr(socket, "AF_UNIX", object()):
            _check(_address_host(address))
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        _check(host)
        return real_getaddrinfo(host, *args, **kwargs)

    def gethostbyname(host):  # type: ignore[no-untyped-def]
        _check(host)
        return real_gethostbyname(host)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    socket.gethostbyname = gethostbyname  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# In-place run guard
# ---------------------------------------------------------------------------

WORKSPACE_MARKER = ".artha-test-workspace"
_HERMETIC_DIRS = ("tests/dev/", "tests/catchup_core/")
_TEMPLATE_PREFIXES = ("state/templates/", "state/learned_procedures/")


def live_data_present(root: Path) -> bool:
    """True when ``root`` holds live Artha data (anything beyond committed templates)."""
    root = Path(root)
    for rel in ("state", "briefings", "summaries", "tmp", "backups"):
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.name in (".gitkeep", ".DS_Store"):
                continue
            posix = path.relative_to(root).as_posix()
            if posix.startswith(_TEMPLATE_PREFIXES):
                continue
            return True
    return False


def inplace_run_allowed(root: Path, selected: list[str], environ: dict[str, str] | None = None) -> bool:
    """May this session run against ``root``?

    Allowed when: the root is an isolated workspace (marker), the owner opted in,
    only self-isolating suites are selected, or the checkout holds no live data
    (a CI checkout).  Everything else is refused: the legacy suite writes to the
    production data directories under the repository root.
    """
    environ = os.environ if environ is None else environ
    if (Path(root) / WORKSPACE_MARKER).exists() or environ.get("ARTHA_ALLOW_INPLACE_TESTS") == "1":
        return True
    if selected and all(s.replace("\\", "/").startswith(_HERMETIC_DIRS) for s in selected):
        return True
    return not live_data_present(root)


# ---------------------------------------------------------------------------
# Protected production-path snapshot
# ---------------------------------------------------------------------------

def _fingerprint(path: Path) -> tuple[int, int, str]:
    stat = path.stat()
    digest = ""
    if stat.st_size <= _HASH_LIMIT:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (stat.st_size, stat.st_mtime_ns, digest)


class ProtectedSnapshot:
    """Fingerprint of production directories, to detect a test touching them."""

    def __init__(self, root: Path, rels: list[str], entries: dict[str, tuple[int, int, str]]):
        self.root = root
        self.rels = rels
        self.entries = entries

    @classmethod
    def take(cls, root: Path, rels: list[str] | tuple[str, ...]) -> "ProtectedSnapshot":
        root = Path(root)
        return cls(root, list(rels), cls._scan(root, list(rels)))

    @staticmethod
    def _scan(root: Path, rels: list[str]) -> dict[str, tuple[int, int, str]]:
        entries: dict[str, tuple[int, int, str]] = {}
        for rel in rels:
            base = root / rel
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for name in filenames:
                    if name == ".DS_Store" or name.endswith(".pyc"):
                        continue
                    path = Path(dirpath) / name
                    try:
                        entries[path.relative_to(root).as_posix()] = _fingerprint(path)
                    except OSError:
                        continue
        return entries

    def diff(self) -> dict[str, list[str]]:
        now = self._scan(self.root, self.rels)
        before = self.entries
        added = sorted(set(now) - set(before))
        deleted = sorted(set(before) - set(now))
        modified = sorted(
            p for p in set(now) & set(before)
            if now[p][0] != before[p][0]
            or (now[p][2] and before[p][2] and now[p][2] != before[p][2])
            or (not now[p][2] and now[p][1] != before[p][1])
        )
        return {"modified": modified, "deleted": deleted, "added": added}
