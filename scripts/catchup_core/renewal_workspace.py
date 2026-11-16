"""Source-only Git/host adapter. Never opens household State or executes candidate code."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from catchup_core.quarantine_fs_windows import WindowsQuarantineDirectory
from catchup_core.renewal_inputs import SourceFile, SourceManifest
from catchup_core.renewal_store import RenewalError, digest

_MAX_FILE = 16 * 1024 * 1024
_SELECTION = ('scripts', 'tools', 'tests', '.github/workflows', 'pyproject.toml', 'artha.py', 'Makefile', '.githooks',
              '.github/copilot-instructions.md', 'config/workflow/development.md', '.gitattributes', '.gitignore')
_LOCAL_INPUTS = ('config/actions.yaml', 'config/user_profile.yaml', 'config/connectors.yaml')
_MAX_SOURCE = 64 * 1024 * 1024


@lru_cache(maxsize=32)
def _tool_digest(path: str, revision: tuple[int, int, int, int, int]) -> str:
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    if _revision(Path(path)) != revision:
        raise RenewalError('release toolchain changed during inspection')
    return result.hexdigest()


def _revision(path: Path) -> tuple[int, int, int, int, int]:
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def context(root: Path | None = None) -> str:
    versions = {}
    for name in ('cryptography', 'PyYAML', 'keyring', 'jsonschema', 'pydantic',
                 'pytest', 'pytest-cov', 'mypy', 'ruff', 'diff-cover'):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = 'missing'
    git_config = Workspace(root).git('config', '--null', '--list', '--show-origin') if root is not None else ''
    tools = {}
    for name in ('git', 'gh', 'make', 'ruff'):
        location = shutil.which(name)
        path = Path(location).resolve() if location else None
        tools[name] = _tool_digest(str(path), _revision(path)) if path is not None else 'missing'
    return digest({'python': sys.version, 'executable': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                   'versions': versions, 'platform': sys.platform, 'tools': tools,
                   'flags': {'optimize': sys.flags.optimize, 'debug': sys.flags.debug},
                   'git_config': hashlib.sha256(git_config.encode()).hexdigest(),
                   'environment': {key: value for key, value in os.environ.items()
                                   if key.startswith(('PYTHON', 'ARTHA', 'GIT'))
                                   or key in ('PATH', 'LANG', 'LC_ALL', 'TZ', 'DEVELOPER_DIR', 'SDKROOT')}})


def source_file(path: Path, relative: str) -> SourceFile:
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise RenewalError('source aliases are not eligible for renewal')
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _MAX_FILE:
        raise RenewalError('source file is unsafe or exceeds its bound')
    if sys.platform == 'win32':
        with (WindowsQuarantineDirectory.open(path.parent, owner_private=False) as directory,
              directory.open_file(path.name, private=False) as stream):
            raw = stream.read(_MAX_FILE + 1)
    else:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise RenewalError('source identity changed')
            raw = stream.read(_MAX_FILE + 1)
    after = path.lstat()
    if stat.S_ISLNK(after.st_mode) or after.st_nlink != 1:
        raise RenewalError('source identity changed')
    if (len(raw) != before.st_size or len(raw) > _MAX_FILE
            or (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_ctime_ns)):
        raise RenewalError('source changed during inspection')
    mode = '100755' if before.st_mode & stat.S_IXUSR else '100644'
    return SourceFile(relative, hashlib.sha256(raw).hexdigest(), mode)


class Workspace:
    def __init__(self, root: Path, *,
                 runner: Callable[..., subprocess.CompletedProcess[bytes]] | None = None):
        self.run = runner or subprocess.run
        self.root = root.absolute()

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        try:
            result = self.run(
                ['git', '-c', 'core.fsmonitor=false', '-C', str(self.root), *args],
                capture_output=True, timeout=120, check=False, env=env)
            if result.returncode or len(result.stdout) > 1024 * 1024:
                raise RenewalError('Git delivery operation failed or exceeded its bound')
            return result.stdout.decode('utf-8').strip()
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            raise RenewalError('Git delivery operation unavailable') from None

    def source(self, extra: tuple[str, ...] = ()) -> dict[str, Any]:
        try:
            listing = self.git('ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', *_SELECTION)
            names = set(listing.split('\0')) - {''}
            names.update(extra)
            names.update(name for name in _LOCAL_INPUTS if (self.root / name).exists())
            # Validate every relative path before the first filesystem content access.
            for name in names:
                SourceFile(name, '0' * 64, '100644')
            if sum((self.root / name).lstat().st_size for name in names) > _MAX_SOURCE:
                raise RenewalError('source closure exceeds its byte bound')
            manifest = SourceManifest(tuple(source_file(self.root / name, name) for name in sorted(names)))
            return cast(dict[str, Any], json.loads(manifest.encode()))
        except (OSError, ValueError):
            raise RenewalError('source manifest could not be established') from None

    def tree(self) -> str:
        if self.git('rev-parse', '--show-toplevel') != str(self.root):
            raise RenewalError('delivery requires the canonical checkout identity')
        if self.git('symbolic-ref', '--short', 'HEAD') != 'main':
            raise RenewalError('delivery requires canonical main')
        if self.git('diff', '--name-only') or self.git('ls-files', '--others', '--exclude-standard'):
            raise RenewalError('stage the exact candidate before delivery')
        index = Path(self.git('rev-parse', '--git-path', 'index'))
        if not index.is_absolute():
            index = self.root / index
        before = index.read_bytes()
        with tempfile.TemporaryDirectory(prefix='artha-delivery-index-') as directory:
            copy = Path(directory) / 'index'
            shutil.copyfile(index, copy)
            tree = self.git('write-tree', env={**os.environ, 'GIT_INDEX_FILE': str(copy)})
        if index.read_bytes() != before:
            raise RenewalError('Git index changed during candidate preparation')
        return tree

    def clean_commit(self) -> str:
        if self.git('status', '--porcelain', '--untracked-files=normal'):
            raise RenewalError('delivery needs a clean committed candidate')
        if self.git('symbolic-ref', '--short', 'HEAD') != 'main':
            raise RenewalError('delivery requires main')
        return self.git('rev-parse', 'HEAD')

    def remote(self, repository: str) -> None:
        if self.git('remote', 'get-url', 'origin') not in (
                'https://github.com/' + repository, 'https://github.com/' + repository + '.git'):
            raise RenewalError('delivery remote differs from the approved repository')

    def remote_tip(self) -> str:
        value = self.git('ls-remote', '--heads', 'origin', 'refs/heads/main').split()
        if len(value) != 2 or value[1] != 'refs/heads/main':
            raise RenewalError('remote main tip unavailable')
        return value[0]

    def assert_tree(self, source: dict[str, Any], tree: str) -> None:
        """CI's committed blobs must equal local source bytes, without Git filter normalization."""
        manifest = SourceManifest.parse(json.dumps(source).encode())
        tracked = {}
        for record in self.git('ls-tree', '-r', '-z', tree).split('\0'):
            if record:
                header, path = record.split('\t', 1)
                mode, kind, identity = header.split()
                tracked[path] = (mode, kind, identity)
        selected = [entry for entry in manifest.files if entry.path in tracked]
        objects = []
        for entry in selected:
            mode, kind, identity = tracked[entry.path]
            if mode != entry.mode or kind != 'blob':
                raise RenewalError('committed source type differs from the validated candidate')
            objects.append(identity)
        query = ''.join(value + '\n' for value in objects).encode('ascii')
        metadata = self._batch('--batch-check', query)
        try:
            sizes = [int(line.split()[2]) for line in metadata.splitlines()]
            if (len(sizes) != len(objects) or any(size < 0 or size > _MAX_FILE for size in sizes)
                    or sum(sizes) > _MAX_SOURCE):
                raise ValueError
            bodies = self._batch('--batch', query)
            offset = 0
            for entry, size in zip(selected, sizes, strict=True):
                line = bodies.index(b'\n', offset)
                body = bodies[line + 1:line + 1 + size]
                if hashlib.sha256(body).hexdigest() != entry.sha256:
                    raise RenewalError('committed source bytes differ from the validated candidate')
                offset = line + size + 2
            if offset != len(bodies):
                raise ValueError
        except (ValueError, IndexError):
            raise RenewalError('committed source inspection is incomplete') from None

    def _batch(self, option: str, query: bytes) -> bytes:
        try:
            result = self.run(
                ['git', '-C', str(self.root), 'cat-file', option],
                input=query, capture_output=True, timeout=120, check=False)
            if result.returncode or len(result.stdout) > _MAX_SOURCE + 1024 * 1024:
                raise RenewalError('committed source inspection exceeded its bound')
            return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            raise RenewalError('committed source inspection unavailable') from None
