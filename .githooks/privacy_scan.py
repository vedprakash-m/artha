#!/usr/bin/env python3
"""
privacy_scan.py — shared privacy gate for pre-commit, pre-push, make and CI
===========================================================================
One policy, four entry points (re-artha §12.9 WP-00, §12.11):

    privacy_scan.py --staged                 # pre-commit: the index
    privacy_scan.py --range <rev-list args>  # pre-push / CI: every commit in a range
    privacy_scan.py --tracked                # make pii-scan: every tracked file at HEAD
    privacy_scan.py --unpushed               # make pii-scan: commits not on any remote
    privacy_scan.py --history                # one-time audit: every commit reachable from any ref

Layers
------
1. Forbidden names and directories (any depth, any commit in a range).
2. Secret and fast-PII regexes over content, regardless of extension.
3. ``scripts/pii_guard.py`` deep scan of text content.
4. gitleaks over the same commits.

The gate **fails closed**: a missing or crashing scanner is a finding, never a
pass.  Nothing here has a bypass switch; the only exemptions are the reviewed
lists below, which are exact paths.
"""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

ARTHA_DIR = Path(__file__).resolve().parent.parent
PII_GUARD = ARTHA_DIR / "scripts" / "pii_guard.py"
ZERO_SHA = "0" * 40

# ─── Layer 1: forbidden names ───────────────────────────────────────────────
FORBIDDEN_PATTERNS = [
    "config/user_profile.yaml",
    "config/artha_config.yaml",
    "config/Artha.identity.md",
    "config/routing.yaml",
    "config/catchup_core.yaml",
    "config/contacts.md",
    "config/contacts.md.age",
    ".artha-decrypted",
    "*.artha-decrypted",          # OneDrive conflict copies of the vault lock marker
    ".tokens/*",
    "*.token.json",
    "*token*.json",
    "*creds*.json",
    "*credentials*.json",
    "*.jsonl",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
]

FORBIDDEN_DIR_PREFIXES = [
    "briefings/",
    "summaries/",
    "tmp/",
    ".tokens/",
]

# State files: block everything in state/ except templates/, learned_procedures/ and .gitkeep
STATE_BLOCK_RE = re.compile(r"^state/(?!templates/|learned_procedures/|\.gitkeep$)")

# ─── Layer 2: secret patterns ───────────────────────────────────────────────
SECRET_PATTERNS = [
    # Full bech32 key body (58 chars); the bare prefix appears in code and docs.
    (re.compile(r"AGE-SECRET-KEY-1[QPZRY9X8GF2TVDW0S3JN54KHCE6MUA7L]{58}"), "age private key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}"), "GitHub token"),
    (re.compile(r'"client_secret"\s*:\s*"[A-Za-z0-9_\-]{20,}"'), "OAuth client secret"),
    (re.compile(r"-----BEGIN\s+(RSA|EC|DSA|OPENSSH)?\s*PRIVATE KEY-----"), "private key (PEM)"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "OpenAI API key"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{35}"), "Google API key"),
]

PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),
    (re.compile(r"\b9\d{2}-[789]\d-\d{4}\b"), "ITIN"),
    (re.compile(r"\b4\d{3}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "Visa card"),
    (re.compile(r"\b5[1-5]\d{2}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "Mastercard"),
    (re.compile(r"\b3[47]\d{2}[\s\-]?\d{6}[\s\-]?\d{5}\b"), "Amex"),
]

ALLOWLIST_PATTERNS = [
    re.compile(r"\d{3}-\d{7}-\d{7}"),    # Amazon order number
    re.compile(r"\*{4}\d{4}"),            # masked card (already redacted)
    re.compile(r"[A-Z]{3}\d{10}"),        # USCIS receipt numbers
]

SKIP_SUFFIXES = {
    ".pyc", ".png", ".jpg", ".jpeg", ".gif", ".pdf",
    ".zip", ".tar", ".gz", ".bin", ".exe",
    ".ico", ".woff", ".woff2", ".ttf", ".eot",
}

# Exact-path exemptions from Layer 1 (reviewed; new fixtures are added here in the same change).
FORBIDDEN_EXEMPT = {
    "tests/fixtures/mock_emails.jsonl",
    "tests/fixtures/finance/emails.jsonl",
    "tests/fixtures/health/emails.jsonl",
    "tests/fixtures/home/emails.jsonl",
    "tests/fixtures/immigration/emails.jsonl",
    "tests/fixtures/kids/emails.jsonl",
    "tests/fixtures/correction_corpus.jsonl",   # reviewed 2026-10-11: synthetic correction phrases
}

# Files that contain PII/secret patterns by design (regex definitions, fake fixtures).
# Layer 1 still applies; only content layers are skipped.
PII_SCAN_EXEMPT = {
    "scripts/pii_guard.py",
    "scripts/backup.py",
    "tests/unit/test_pii_guard.py",
    "tests/unit/test_backup.py",
    "tests/unit/test_vault.py",
    "tests/unit/test_fact_extractor.py",
    "tests/integration/test_catch_up_e2e.py",
    "tests/pii_test_data.txt",
    "specs/supercharge-reloaded.md",
    "archive/bkp-rst.md",
    ".githooks/pre-commit",
    ".githooks/pre-push",
    ".githooks/privacy_scan.py",
    "tests/dev/test_privacy_gate.py",
}


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str
    where: str = ""        # commit sha for range scans

    def render(self) -> str:
        loc = f" @{self.where[:10]}" if self.where else ""
        return f"{self.path}{loc}  [{self.reason}]"


# ─── git plumbing ───────────────────────────────────────────────────────────

def git_env() -> dict[str, str]:
    """Environment for git subprocesses.

    On macOS ``/usr/bin/git`` is a shim that refuses to run until the Xcode
    licence is accepted (re-artha D-01).  Prefer the Command Line Tools.
    """
    env = dict(os.environ)
    clt = Path("/Library/Developer/CommandLineTools")
    if sys.platform == "darwin" and (clt / "usr/bin/git").exists():
        env.setdefault("DEVELOPER_DIR", str(clt))
    return env


def run_git(args: list[str], cwd: Path, *, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=text,
        encoding="utf-8" if text else None, errors="replace" if text else None,
        env=git_env(),
    )


def _git_ok(args: list[str], cwd: Path) -> str:
    result = run_git(args, cwd)
    if result.returncode != 0:
        raise ScannerError(f"git {' '.join(args)} failed: {result.stderr.strip()[:200]}")
    return result.stdout


class ScannerError(RuntimeError):
    """A scanner could not run.  Always converted into a blocking finding."""


# ─── content rules ──────────────────────────────────────────────────────────

def check_forbidden(rel_path: str) -> str | None:
    if rel_path in FORBIDDEN_EXEMPT:
        return None
    name = rel_path.rsplit("/", 1)[-1]
    for pattern in FORBIDDEN_PATTERNS:
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(name, pattern):
            return f"forbidden file pattern: {pattern}"
    for prefix in FORBIDDEN_DIR_PREFIXES:
        if rel_path.startswith(prefix) or f"/{prefix}" in rel_path:
            return f"forbidden directory: {prefix}"
    if STATE_BLOCK_RE.match(rel_path):
        return "state file (user data)"
    if rel_path.endswith(".age"):
        return "encrypted .age file"
    return None


# Published example values (SSA advertising SSN, card-network test numbers, all-zero
# placeholders).  Matching is by digits only, so this never exempts a real number.
SYNTHETIC_VALUES = {
    "123456789", "000000000", "987654321", "078051120",
    "4111111111111111", "4242424242424242", "4012888888881881",
    "5555555555554444", "5105105105105100", "378282246310005", "371449635398431",
}


def _is_allowlisted(text: str, start: int, end: int) -> bool:
    if re.sub(r"\D", "", text[start:end]) in SYNTHETIC_VALUES:
        return True
    window = text[max(0, start - 20): min(len(text), end + 20)]
    return any(p.search(window) for p in ALLOWLIST_PATTERNS)


def check_secrets(content: str) -> list[str]:
    findings: list[str] = []
    for pattern, label in SECRET_PATTERNS:
        if pattern.search(content):
            findings.append(f"secret ({label})")
    for pattern, label in PII_PATTERNS:
        for m in pattern.finditer(content):
            if not _is_allowlisted(content, m.start(), m.end()):
                findings.append(f"PII ({label})")
                break
    return findings


def check_pii_guard(content: str, guard: Path = PII_GUARD) -> str | None:
    """Layer 3.  Fails closed when the guard is missing or crashes."""
    if not guard.exists():
        return "scanner unavailable: scripts/pii_guard.py missing (fail closed)"
    try:
        result = subprocess.run(
            [sys.executable, str(guard), "scan"], input=content, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=60,
            env={**os.environ, "ARTHA_PII_DOC_SCAN": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"scanner unavailable: pii_guard failed to run ({type(exc).__name__}) (fail closed)"
    if result.returncode == 0:
        return None
    for line in (result.stderr or "").splitlines():
        if line.startswith("PII_FOUND:"):
            return line[len("PII_FOUND:"):].strip() or "PII detected"
    return "PII detected"


TEXT_SCAN_SUFFIXES = (".md", ".yaml", ".yml", ".txt")


def scan_item(rel_path: str, content: str | None, where: str = "",
              guard: Path = PII_GUARD, denylist: Denylist | None = None) -> list[Finding]:
    """Scan one file version.  ``content`` is None for unreadable blobs."""
    reason = check_forbidden(rel_path)
    if reason:
        return [Finding(rel_path, reason, where)]
    if Path(rel_path).suffix.lower() in SKIP_SUFFIXES:
        return []
    if rel_path in PII_SCAN_EXEMPT or not content:
        return []
    findings = [Finding(rel_path, r, where) for r in check_secrets(content)]
    if denylist:
        findings += [Finding(rel_path, r, where) for r in denylist.match(content)]
    if rel_path.endswith(TEXT_SCAN_SUFFIXES) and not findings:
        pii = check_pii_guard(content, guard)
        if pii:
            findings.append(Finding(rel_path, f"pii_guard: {pii}", where))
    return findings


# ─── Layer 2c: owner-specific identifiers (local only) ──────────────────────
# Regexes cannot recognise a person's name, address or handle.  The owner's own
# profile (gitignored, never committed) is the authoritative list of what must
# not leave the machine, so its identifying values become a denylist.  Matches
# are reported by category and a short digest, never by value.
_IDENT_KEYS_BLOCK = {
    "dob", "phone", "vin", "primary_chat_id",
    "spouse_chat_id", "bot_username", "age_recipient", "canvas_url", "email_domain",
    "address", "street", "ssn", "passport_number", "policy_number", "account_number",
    "member_id",
}
_EMAIL_KEYS = {"email", "gmail", "outlook", "icloud"}
_NAME_KEYS = {"name", "full_name", "legal_name", "spouse_name", "display_name"}
_SUBSECTION = re.compile(r"^  ([A-Za-z_][\w-]*)\s*:\s*$")
# Household members other than the owner: a first name alone identifies them, so it is denylisted too.
_HOUSEHOLD_SUBSECTIONS = {"spouse", "children", "siblings", "extended_family"}
_YAML_LINE = re.compile(r"^\s*(?:-\s+)?([A-Za-z_][\w-]*)\s*:\s*(.+?)\s*(?:#.*)?$")
_PLACEHOLDER = re.compile(r"^(?:\$\{.*\}|<.*>|your[-_ ].*|example.*|changeme|todo|tbd|none|null|true|false|yes|no)$", re.I)


def _is_household_member_name(line: str, subsection: str) -> bool:
    """True for the ``name`` of a spouse, child, sibling or relative — not of their school or place."""
    if subsection not in _HOUSEHOLD_SUBSECTIONS:
        return False
    indent = len(line) - len(line.lstrip())
    if subsection == "spouse":
        return indent == 4
    return line.lstrip().startswith("- ")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def _phone_regex(digits: str) -> str:
    return r"(?<!\d)" + r"[\s().\-]*".join(re.escape(d) for d in digits) + r"(?!\d)"


class Denylist:
    """Compiled owner identifiers: ``[(category, digest, compiled regex)]``."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str, re.Pattern[str]]] = []
        self._seen: set[str] = set()

    def add(self, category: str, term: str) -> None:
        term = term.strip()
        if len(term) < 4 or term.lower() in self._seen or _PLACEHOLDER.match(term):
            return
        self._seen.add(term.lower())
        digits = re.sub(r"\D", "", term)
        if category == "phone" and len(digits) >= 10:
            pattern = re.compile(_phone_regex(digits[-10:]))
        else:
            flexible = r"\s+".join(re.escape(part) for part in term.split())
            pattern = re.compile(rf"(?<![A-Za-z0-9]){flexible}(?![A-Za-z0-9])", re.IGNORECASE)
        import hashlib  # noqa: PLC0415
        digest = hashlib.sha256(term.lower().encode()).hexdigest()[:6]
        self.entries.append((category, digest, pattern))

    def __bool__(self) -> bool:
        return bool(self.entries)

    def match(self, content: str) -> list[str]:
        return [f"personal identifier ({cat}) #{digest}"
                for cat, digest, pat in self.entries if pat.search(content)]


def load_denylist(repo: Path = ARTHA_DIR) -> Denylist:
    deny = Denylist()
    profile = repo / "config" / "user_profile.yaml"
    try:
        text = profile.read_text(encoding="utf-8") if profile.is_file() else ""
    except OSError:
        text = ""
    section = ""
    subsection = ""
    for line in text.splitlines():
        top = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*$", line)
        if top:
            section, subsection = top.group(1).lower(), ""
            continue
        nested = _SUBSECTION.match(line)
        if nested:
            subsection = nested.group(1).lower()
            continue
        m = _YAML_LINE.match(line)
        if not m:
            continue
        key, raw = m.group(1).lower(), _unquote(m.group(2))
        if not raw or raw.startswith(("[", "{", "|", ">")):
            continue
        if key in _EMAIL_KEYS:
            if "@" in raw:                 # provider names ("msgraph") are not identifiers
                deny.add("email", raw)
        elif key in _IDENT_KEYS_BLOCK:
            deny.add("phone" if key == "phone" else key, raw)
        elif key in _NAME_KEYS and section == "family":
            deny.add("name" if len(raw.split()) >= 2 else "given-name", raw)
            if _is_household_member_name(line, subsection):
                deny.add("given-name", raw.split()[0].strip(".,"))
    extra = Path(os.environ.get("ARTHA_LOCAL_DIR", str(Path.home() / ".artha-local"))).expanduser() \
        / "privacy_denylist.txt"
    try:
        for line in extra.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                deny.add("listed", line)
    except OSError:
        pass
    return deny


_DENYLIST_CACHE: dict[Path, Denylist] = {}


def denylist_for(repo: Path) -> Denylist:
    key = Path(repo).resolve()
    if key not in _DENYLIST_CACHE:
        _DENYLIST_CACHE[key] = load_denylist(key)
    return _DENYLIST_CACHE[key]


# ─── git-backed enumeration ────────────────────────────────────────────────

def staged_items(repo: Path) -> Iterator[tuple[str, str | None, str]]:
    names = _git_ok(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"], repo)
    for rel in (n for n in names.split("\0") if n):
        blob = run_git(["show", f":{rel}"], repo)
        yield rel, (blob.stdout if blob.returncode == 0 else None), ""


def tracked_items(repo: Path) -> Iterator[tuple[str, str | None, str]]:
    names = _git_ok(["ls-files", "-z"], repo)
    for rel in (n for n in names.split("\0") if n):
        path = repo / rel
        try:
            data = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else None
        except OSError:
            data = None
        yield rel, data, ""


def commits_in(repo: Path, rev_args: list[str]) -> list[str]:
    out = _git_ok(["rev-list", "--reverse", *rev_args], repo)
    return [c for c in out.split() if c]


def commit_items(repo: Path, commit: str) -> Iterator[tuple[str, str | None, str]]:
    names = _git_ok(
        ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m",
         "--diff-filter=ACMR", "-z", commit], repo)
    seen: set[str] = set()
    for rel in (n for n in names.split("\0") if n):
        if rel in seen:
            continue
        seen.add(rel)
        blob = run_git(["show", f"{commit}:{rel}"], repo)
        yield rel, (blob.stdout if blob.returncode == 0 else None), commit


def scan_items(items: Iterable[tuple[str, str | None, str]], guard: Path = PII_GUARD,
               repo: Path = ARTHA_DIR) -> list[Finding]:
    deny = denylist_for(repo)
    findings: list[Finding] = []
    for rel, content, where in items:
        findings.extend(scan_item(rel, content, where, guard, deny))
    return findings


# ─── gitleaks ───────────────────────────────────────────────────────────────

def gitleaks_binary() -> str | None:
    return (
        os.environ.get("ARTHA_GITLEAKS")
        or shutil.which("gitleaks")
        or ("/opt/homebrew/bin/gitleaks" if os.path.isfile("/opt/homebrew/bin/gitleaks") else None)
        or ("/usr/local/bin/gitleaks" if os.path.isfile("/usr/local/bin/gitleaks") else None)
    )


def _gitleaks_findings(report: str) -> list[Finding]:
    """One Finding per leak, naming rule, file and commit (never the secret)."""
    import json  # noqa: PLC0415
    try:
        leaks = json.loads(report or "[]")
    except ValueError:
        return [Finding("(gitleaks)", "secret detected (unparsable report)")]
    if not leaks:
        return [Finding("(gitleaks)", "secret detected")]
    return [Finding(str(leak.get("File", "(unknown)")),
                    f"secret ({leak.get('RuleID', 'gitleaks')})",
                    str(leak.get("Commit", "")))
            for leak in leaks]


def run_gitleaks(repo: Path, *, staged: bool = False, log_opts: str | None = None,
                 binary: str | None = None) -> list[Finding]:
    exe = binary or gitleaks_binary()
    if not exe or (not os.path.isabs(exe) and shutil.which(exe) is None) or \
            (os.path.isabs(exe) and not os.access(exe, os.X_OK)):
        return [Finding("(gitleaks)", "scanner unavailable: gitleaks not installed (fail closed)")]
    args = [exe, "git", "--no-banner", "--redact", "-l", "error", "-f", "json", "-r", "-"]
    config = repo / ".gitleaks.toml"
    if config.exists():
        args += ["-c", str(config)]
    if staged:
        args += ["--staged"]
    elif log_opts:
        args += [f"--log-opts={log_opts}"]
    args.append(str(repo))
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=600, env=git_env())
    except (OSError, subprocess.SubprocessError) as exc:
        return [Finding("(gitleaks)", f"scanner failed to run ({type(exc).__name__}) (fail closed)")]
    if result.returncode == 0:
        return []
    if result.returncode == 1:
        out = (result.stdout or "").strip()
        if not out.startswith("["):        # config/usage error also exits 1
            return [Finding("(gitleaks)", "scanner error: " + (result.stderr or out)[:200] + " (fail closed)")]
        return _gitleaks_findings(out)
    return [Finding("(gitleaks)", f"scanner error (exit {result.returncode}) (fail closed)")]


# ─── entry points ───────────────────────────────────────────────────────────

def scan_staged(repo: Path = ARTHA_DIR, guard: Path = PII_GUARD, binary: str | None = None) -> list[Finding]:
    try:
        findings = scan_items(staged_items(repo), guard, repo)
        if any(True for _ in staged_items(repo)):
            findings += run_gitleaks(repo, staged=True, binary=binary)
        return findings
    except ScannerError as exc:
        return [Finding("(git)", f"scanner unavailable: {exc} (fail closed)")]


def scan_range(repo: Path, rev_args: list[str], guard: Path = PII_GUARD,
               binary: str | None = None) -> list[Finding]:
    try:
        commits = commits_in(repo, rev_args)
        findings: list[Finding] = []
        for commit in commits:
            findings += scan_items(commit_items(repo, commit), guard, repo)
        if commits:
            findings += run_gitleaks(repo, log_opts=" ".join(rev_args), binary=binary)
        return findings
    except ScannerError as exc:
        return [Finding("(git)", f"scanner unavailable: {exc} (fail closed)")]


def scan_tracked(repo: Path = ARTHA_DIR, guard: Path = PII_GUARD) -> list[Finding]:
    try:
        return scan_items(tracked_items(repo), guard, repo)
    except ScannerError as exc:
        return [Finding("(git)", f"scanner unavailable: {exc} (fail closed)")]


def report(findings: list[Finding], title: str) -> int:
    if not findings:
        print(f"✓  {title}: all checks passed.")
        return 0
    print(f"⛔  {title} BLOCKED — PII, secrets or forbidden files detected:\n")
    for f in sorted(set(findings), key=lambda x: (x.path, x.where, x.reason)):
        print(f"   {f.render()}")
    print("\n   Fix: remove the data (and purge it from history if already committed), then retry.")
    print("   There is no bypass: --no-verify is not an accepted workflow (re-artha §12.8).")
    return 1


def main(argv: list[str]) -> int:
    repo = ARTHA_DIR
    if not argv:
        print(__doc__)
        return 30
    mode = argv[0]
    if mode == "--staged":
        return report(scan_staged(repo), "Pre-commit")
    if mode == "--tracked":
        return report(scan_tracked(repo), "Tracked-file scan")
    if mode == "--unpushed":
        return report(scan_range(repo, ["HEAD", "--not", "--remotes"]), "Unpushed-commit scan")
    if mode == "--range":
        return report(scan_range(repo, argv[1:]), "Range scan")
    if mode == "--history":
        # Not `--all`: a damaged ref (e.g. an OneDrive conflict copy of a branch) must not
        # abort the audit.  main plus every remote-tracking ref and tag is the pushed history.
        refs = [r for r in ("main", "--remotes", "--tags") if r != "main" or
                run_git(["rev-parse", "--verify", "-q", "main"], repo).returncode == 0]
        return report(scan_range(repo, refs), "Full-history audit")
    print(f"unknown mode {mode}")
    return 30


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
