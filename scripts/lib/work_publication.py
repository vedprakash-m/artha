"""Work producer publication adapter (WP-18 local Work protocol).

Before explicit enrollment every helper runs the caller's existing writer unchanged and
touches no native key, identity or credential. After enrollment each supported write is
computed from current bytes inside one ``WorkPrivacy`` transaction and staged through
``tx.publish``; nothing is written to a Work root before that publication.

A producer entry point opens ``session()``: it pins the current head, and a publication
whose transaction finds another writer's head refuses as stale input instead of
overwriting it. One enrolled session is active per process; it belongs to the context
that opened it (worker threads join only through an explicitly copied context), and any
other thread's session or bare write waits until it ends. Paths outside the canonical
``for_installation`` roots are refused.
Batch completion/discard uses the publication guard so joined workers cannot publish
outside staging or lose an update while the owner closes the batch.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar

_REPO = Path(__file__).resolve().parents[2]
_factory: Callable[[], Any] | None = None
_active: _Session | None = None
_current: ContextVar[_Session | None] = ContextVar('work_publication_session', default=None)
_guard = threading.RLock()
_idle = threading.Condition(_guard)
F = TypeVar('F', bound=Callable[..., Any])


class WorkPublicationBlocked(RuntimeError):
    """An enrolled Work write cannot be published through the journaled boundary."""


def set_protocol_factory(factory: Callable[[], Any] | None) -> Callable[[], Any] | None:
    """Inject a synthetic protocol for tests; ``None`` restores the canonical factory."""
    global _factory
    previous, _factory = _factory, factory
    return previous


def protocol() -> Any:
    if _factory is not None:
        return _factory()
    from catchup_core.work_privacy import for_installation  # noqa: PLC0415
    return for_installation(_REPO)


def _probe() -> Any | None:
    """Return the enrolled protocol, or ``None`` for unchanged legacy behaviour."""
    own = _current.get()
    if own is not None:
        return own.protocol
    if _factory is None:
        from catchup_core.session_lock import default_installation_dir  # noqa: PLC0415
        try:
            os.lstat(default_installation_dir() / 'work-privacy')
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise WorkPublicationBlocked('Work enrollment authority cannot be inspected; legacy write refused') from exc
    candidate = protocol()
    return candidate if candidate.is_enrolled() else None


def enrolled() -> bool:
    return _probe() is not None


def covers(path: Path) -> bool:
    """True when enrolled and ``path`` lies under a bound Work root (shared writers route only those)."""
    active = _probe()
    if active is None:
        return False
    try:
        _member(active, path)
    except WorkPublicationBlocked:
        return False
    return True


def staging_dir() -> Path:
    """Owner-private staging outside both Work roots, excluded from capture, escrow and sync."""
    active = _probe()
    if active is None:
        raise WorkPublicationBlocked('Work staging exists only for an enrolled installation')
    return Path(active.local) / 'work-staging'


def refuse_unmediated(what: str) -> None:
    """Refuse a write path the host cannot confine to staged publication."""
    if enrolled():
        raise WorkPublicationBlocked(f'{what} writes Work state outside journaled publication; refused')


@contextmanager
def _translated() -> Iterator[None]:
    from catchup_core.work_privacy import WorkPrivacyBlocked  # noqa: PLC0415
    try:
        yield
    except WorkPrivacyBlocked as exc:
        raise WorkPublicationBlocked(str(exc)) from exc


class _Session:
    def __init__(self, protocol: Any):
        self.protocol = protocol
        self.head = protocol.current()['head']
        self.batch: dict[Path, bytes | None] | None = None


@contextmanager
def _enter() -> Iterator[_Session | None]:
    """Join this context's session, or wait for the process to be idle and open one; ``None`` = legacy."""
    global _active
    own = _current.get()
    if own is not None:
        yield own
        return
    with _guard:
        while _active is not None:
            _idle.wait()
        active = _probe()
        if active is None:
            claimed = None
        else:
            with _translated():
                claimed = _active = _Session(active)
    if claimed is None:
        yield None
        return
    token = _current.set(claimed)
    try:
        yield claimed
    finally:
        _current.reset(token)
        with _guard:
            _active = None
            _idle.notify_all()


@contextmanager
def session() -> Iterator[None]:
    """Pin the current Work head for a producer run; reentrant within its context, serialized across threads."""
    with _enter():
        yield


def in_session(function: F) -> F:
    @wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with session():
            return function(*args, **kwargs)
    return wrapper  # type: ignore[return-value]


@contextmanager
def batch() -> Iterator[None]:
    """Collect every write in the block into one generation, published only if the block succeeds."""
    with _enter() as active:
        with _guard:
            owner = active is not None and active.batch is None
            if owner:
                active.batch = {}
        succeeded = False
        try:
            yield
            succeeded = True
        finally:
            with _guard:
                if owner:
                    staged = dict(active.batch)
                    active.batch = None
                    if succeeded and staged:
                        _commit(active, lambda _read: staged)


def _member(active: Any, path: Path) -> tuple[str, Path]:
    target = Path(path).absolute()
    for alias, root in active.roots.items():
        try:
            relative = target.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if relative.parts:
            return alias + '/' + relative.as_posix(), target
    raise WorkPublicationBlocked('path is outside the bound Work roots')


def _read(active: _Session, path: Path) -> bytes | None:
    if active.batch is not None and Path(path).absolute() in active.batch:
        return active.batch[Path(path).absolute()]
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def encode(text: str) -> bytes:
    """Bytes a text-mode legacy writer produces on this platform."""
    return text.replace('\n', os.linesep).encode('utf-8')


def decode(raw: bytes) -> str:
    return raw.decode('utf-8').replace('\r\n', '\n')


def _commit(active: _Session, build: Callable[[Callable[[Path], bytes | None]], Mapping[Path, bytes | None]]) -> None:
    with _guard, _translated(), active.protocol.transaction() as tx:
        if tx.pinned['head'] != active.head:
            raise WorkPublicationBlocked('Work state changed under this producer; stale input refused')
        changes = {}
        def bound_read(path: Path) -> bytes | None:
            _member(active.protocol, path)
            return _read(active, path)

        for path, raw in build(bound_read).items():
            name, target = _member(active.protocol, path)
            if active.batch is not None:
                active.batch[target] = raw
                continue
            changes[name] = raw
        if not changes:
            return
        active.head = tx.publish(changes)['head']


def publish(changes: Mapping[Path, bytes | None], *, legacy: Callable[[], None]) -> None:
    """Publish exact staged bytes (``None`` deletes) as one generation."""
    with _enter() as active:
        if active is None:
            legacy()
            return
        _commit(active, lambda _read: changes)


def update_bytes(path: Path, compute: Callable[[bytes | None], bytes | None], *,
                 legacy: Callable[[], None]) -> None:
    """Read-modify-write computed from current bytes inside the transaction; ``None`` keeps the file."""
    def build(read: Callable[[Path], bytes | None]) -> dict[Path, bytes | None]:
        updated = compute(read(Path(path)))
        return {} if updated is None else {Path(path): updated}
    with _enter() as active:
        if active is None:
            legacy()
            return
        _commit(active, build)


def publish_text(path: Path, text: str, *, legacy: Callable[[], None]) -> None:
    publish({Path(path): encode(text)}, legacy=legacy)


def append_text(path: Path, text: str, *, legacy: Callable[[], None]) -> None:
    update_bytes(path, lambda current: (current or b'') + encode(text), legacy=legacy)


def update_text(path: Path, compute: Callable[[str | None], str | None], *, legacy: Callable[[], None]) -> None:
    def adapt(current: bytes | None) -> bytes | None:
        updated = compute(None if current is None else decode(current))
        return None if updated is None else encode(updated)
    update_bytes(path, adapt, legacy=legacy)