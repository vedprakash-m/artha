"""Rule of Two (re-artha §6.6, §6.16, RA-18).

A model-call configuration declares three properties:

* **U** — processes untrusted content,
* **P** — can read private data,
* **C** — can cause an outbound or state-changing effect.

At most two may hold.  :func:`lint_configs` fails CI for a declared combination;
:class:`Broker` re-checks the *actual* attachments of every call at runtime, so a
declaration that understates reality is still caught.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Broker", "CallDeclaration", "RuleOfTwoViolation", "lint_configs"]


class RuleOfTwoViolation(RuntimeError):
    """All three of U, P and C hold for one model call."""


@dataclass(frozen=True)
class CallDeclaration:
    """What a model-call path says it does."""

    name: str
    untrusted: bool          # U
    private: bool            # P
    effect: bool             # C

    @property
    def count(self) -> int:
        return int(self.untrusted) + int(self.private) + int(self.effect)

    @property
    def violates(self) -> bool:
        return self.count == 3


#: Effect-capable tool names.  A call that exposes any of these has C.
EFFECT_TOOLS = frozenset({
    "send_email", "send_message", "write_file", "write_state", "http_request", "fetch_url",
    "create_event", "shell", "execute", "commit", "delete", "approve", "propose_commit",
})


def lint_configs(declarations: Iterable[CallDeclaration]) -> list[str]:
    """Return one message per violating configuration (empty means clean)."""
    return [
        f"{d.name}: processes untrusted content, reads private data and can cause an effect "
        "(Rule of Two allows at most two)"
        for d in declarations if d.violates
    ]


class Broker:
    """Runtime check of the *actual* tool list and data attached to each call."""

    def __init__(self, declarations: Mapping[str, CallDeclaration] | None = None):
        self._declared = dict(declarations or {})

    def check(
        self,
        name: str,
        *,
        tools: Sequence[str] = (),
        private_data: bool = False,
        untrusted_data: bool = False,
    ) -> CallDeclaration:
        """Validate one call; returns the effective declaration or raises."""
        declared = self._declared.get(name)
        effective = CallDeclaration(
            name=name,
            untrusted=untrusted_data or bool(declared and declared.untrusted),
            private=private_data or bool(declared and declared.private),
            effect=any(t in EFFECT_TOOLS for t in tools) or bool(declared and declared.effect),
        )
        if effective.violates:
            raise RuleOfTwoViolation(
                f"{name}: untrusted={effective.untrusted} private={effective.private} "
                f"effect={effective.effect} — a call may have at most two"
            )
        return effective
