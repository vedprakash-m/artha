"""Domain pack schema, loader and rule evaluator (re-artha RA-14, Appendix B).

A pack declares, per Domain: sources and freshness, typed predicates with
per-predicate source authority, deterministic alert rules, deadline arithmetic
and cross-Domain cases.  Loading is strict:

* unknown keys, predicate names, rule variables or enum literals fail at load;
* symbolic references (``school_local``, ``school_calendar``) must be bound;
* rule ``when`` expressions are parsed with a whitelist AST — never ``eval`` —
  over the closed variable dictionary.

Rule evaluation is three-valued: a missing variable yields *unknown* (declared
uncertainty), never a guessed default.
"""
from __future__ import annotations

import ast
import operator
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from catchup_core.contracts import Confidentiality, Severity

__all__ = [
    "RULE_VARIABLES",
    "AlertRule",
    "DomainPack",
    "PackError",
    "Predicate",
    "RuleResult",
    "Source",
    "composite_pack",
    "load_pack",
    "load_pack_file",
]

RULE_VARIABLES = frozenset({"value", "days_until", "obligation_state", "fact_status", "source_class", "now"})
_PREDICATE_TYPES = ("date", "datetime", "enum", "text")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_TOP_KEYS = {
    "domain", "version", "confidentiality", "impact", "bindings", "sources", "predicates",
    "alert_rules", "deadline_arithmetic", "cross_domain_cases", "fixtures_dir",
}
_PRED_KEYS = {"name", "type", "values", "high_stakes", "confidentiality", "authority", "lead_days"}
_SOURCE_KEYS = {"connector", "required", "freshness_hours", "class"}
_RULE_KEYS = {"predicate", "when", "severity"}
_ARITH_KEYS = {"timezone", "date_only", "end_dates", "business_days", "latest_useful_action"}


class PackError(ValueError):
    """The pack is malformed, references something unbound, or uses an unknown name."""


@dataclass(frozen=True)
class Source:
    connector: str
    required: bool
    freshness_hours: int
    source_class: str = "fetched"


@dataclass(frozen=True)
class Predicate:
    name: str
    type: str
    authority: tuple[str, ...]
    values: tuple[str, ...] = ()
    high_stakes: bool = False
    confidentiality: Confidentiality | None = None
    lead_days: int = 0

    def validate_value(self, value: Any) -> None:
        """Typed check of a candidate value (discriminated by ``type``)."""
        if self.type == "enum":
            if value not in self.values:
                raise PackError(f"{self.name}: {value!r} not in {list(self.values)}")
        elif self.type == "date":
            if not isinstance(value, date) or isinstance(value, datetime):
                raise PackError(f"{self.name}: expected a date, got {type(value).__name__}")
        elif self.type == "datetime":
            if not isinstance(value, datetime):
                raise PackError(f"{self.name}: expected a datetime, got {type(value).__name__}")
        elif self.type == "text" and not isinstance(value, str):
            raise PackError(f"{self.name}: expected text, got {type(value).__name__}")


@dataclass(frozen=True)
class AlertRule:
    predicate: str
    when: str
    severity: Severity
    _tree: ast.Expression = field(repr=False, compare=False, default=None)  # type: ignore[assignment]


@dataclass(frozen=True)
class RuleResult:
    severity: Severity | None
    uncertain: bool                       # a rule could not be evaluated for lack of a variable
    matched: tuple[str, ...] = ()         # the `when` texts that fired


@dataclass(frozen=True)
class DomainPack:
    domain: str
    version: str
    confidentiality: Confidentiality
    impact: str
    sources: tuple[Source, ...]
    predicates: Mapping[str, Predicate]
    rules: tuple[AlertRule, ...]
    timezone: tzinfo
    timezone_name: str
    holidays: frozenset[date]
    end_dates: str
    cross_domain_cases: tuple[str, ...]
    fixtures_dir: str

    # ── rules ────────────────────────────────────────────────────────────
    def evaluate(self, predicate: str, variables: Mapping[str, Any]) -> RuleResult:
        """Most severe matching rule wins; unknown variables mark the result uncertain."""
        if predicate not in self.predicates:
            raise PackError(f"unknown predicate {predicate!r}")
        unknown = set(variables) - RULE_VARIABLES
        if unknown:
            raise PackError(f"unknown rule variables: {sorted(unknown)}")
        best: Severity | None = None
        matched: list[str] = []
        uncertain = False
        for rule in self.rules:
            if rule.predicate != predicate:
                continue
            outcome = _evaluate(rule._tree.body, variables)
            if outcome is None:
                uncertain = True
            elif outcome:
                matched.append(rule.when)
                if best is None or rule.severity.rank < best.rank:
                    best = rule.severity
        return RuleResult(best, uncertain, tuple(matched))

    # ── deadline arithmetic ──────────────────────────────────────────────
    def days_until(self, deadline: date | datetime, now: datetime) -> int:
        """Whole calendar days from ``now`` to ``deadline`` in the pack's timezone.

        A date-only deadline carries no time of day (it is the whole day), so only
        calendar dates are compared.
        """
        if now.tzinfo is None:
            raise PackError("now must be timezone-aware")
        local_now = now.astimezone(self.timezone).date()
        if isinstance(deadline, datetime):
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=self.timezone)
            target = deadline.astimezone(self.timezone).date()
        else:
            target = deadline
        return (target - local_now).days

    def last_inclusive_day(self, end: date) -> date:
        """Exclusive end dates (all-day events) name the day after the last day."""
        return end - timedelta(days=1) if self.end_dates == "exclusive" else end

    def latest_useful_action(self, predicate: str, due: date) -> date:
        """``due`` minus the predicate's lead time, counted in school business days."""
        lead = self.predicates[predicate].lead_days
        day = due
        remaining = lead
        while remaining > 0:
            day -= timedelta(days=1)
            if day.weekday() < 5 and day not in self.holidays:
                remaining -= 1
        return day


# ── expression evaluation (whitelisted AST, three-valued) ─────────────────

_CMP: dict[type, Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


def _check_node(node: ast.AST, names: set[str]) -> None:
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare,
               ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple, *tuple(_CMP))
    if not isinstance(node, allowed):
        raise PackError(f"unsupported syntax in rule: {type(node).__name__}")
    if isinstance(node, ast.Name):
        if node.id not in RULE_VARIABLES:
            raise PackError(f"unknown rule variable {node.id!r} (allowed: {sorted(RULE_VARIABLES)})")
        names.add(node.id)
    if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float, str, bool)):
        raise PackError("only numbers, strings and booleans are allowed as literals")
    for child in ast.iter_child_nodes(node):
        _check_node(child, names)


def _evaluate(node: ast.AST, variables: Mapping[str, Any]) -> bool | None:
    value = _value(node, variables)
    if value is _UNKNOWN:
        return None
    return bool(value)


class _Unknown:
    pass


_UNKNOWN = _Unknown()


def _bool_op(node: ast.BoolOp, variables: Mapping[str, Any]) -> Any:
    results = [_value(v, variables) for v in node.values]
    unknown = any(r is _UNKNOWN for r in results)
    if isinstance(node.op, ast.And):
        if any(r is not _UNKNOWN and not r for r in results):
            return False
        return _UNKNOWN if unknown else True
    if any(r is not _UNKNOWN and r for r in results):
        return True
    return _UNKNOWN if unknown else False


def _unary_op(node: ast.UnaryOp, variables: Mapping[str, Any]) -> Any:
    operand = _value(node.operand, variables)
    if operand is _UNKNOWN:
        return _UNKNOWN
    return (not operand) if isinstance(node.op, ast.Not) else -operand


def _collection(node: ast.List | ast.Tuple, variables: Mapping[str, Any]) -> Any:
    items = [_value(e, variables) for e in node.elts]
    return _UNKNOWN if any(i is _UNKNOWN for i in items) else items


def _compare(node: ast.Compare, variables: Mapping[str, Any]) -> Any:
    left = _value(node.left, variables)
    for op, comparator in zip(node.ops, node.comparators, strict=True):
        right = _value(comparator, variables)
        if left is _UNKNOWN or right is _UNKNOWN:
            return _UNKNOWN
        try:
            if not _CMP[type(op)](left, right):
                return False
        except TypeError:
            return _UNKNOWN
        left = right
    return True


def _value(node: ast.AST, variables: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        got = variables.get(node.id)
        return _UNKNOWN if got is None else got
    handlers: tuple[tuple[type, Any], ...] = (
        (ast.BoolOp, _bool_op), (ast.UnaryOp, _unary_op), (ast.Compare, _compare),
        (ast.List, _collection), (ast.Tuple, _collection),
    )
    for node_type, handler in handlers:
        if isinstance(node, node_type):
            return handler(node, variables)
    raise PackError(f"unsupported syntax in rule: {type(node).__name__}")   # pragma: no cover


# ── loading ───────────────────────────────────────────────────────────────

def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = set(mapping) - allowed
    if extra:
        raise PackError(f"{where}: unknown key(s) {sorted(extra)}")


def _resolve_timezone(ref: str, bindings: Mapping[str, Any]) -> tuple[tzinfo, str]:
    name = bindings.get(ref, ref) if isinstance(bindings.get(ref, ref), str) else ref
    try:
        return ZoneInfo(str(name)), str(name)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise PackError(f"timezone reference {ref!r} is unbound or not a valid IANA zone") from exc


def _holidays(ref: str, bindings: Mapping[str, Any]) -> frozenset[date]:
    if ref == "weekdays":
        return frozenset()
    bound = bindings.get(ref)
    if not isinstance(bound, Mapping) or "holidays" not in bound:
        raise PackError(f"business_days reference {ref!r} is unbound")
    try:
        return frozenset(date.fromisoformat(str(d)) for d in bound["holidays"])
    except ValueError as exc:
        raise PackError(f"business_days {ref!r}: bad holiday date") from exc


def _load_sources(raw_sources: Any) -> tuple[Source, ...]:
    sources: list[Source] = []
    for raw in raw_sources:
        _reject_unknown(raw, _SOURCE_KEYS, "source")
        if "connector" not in raw:
            raise PackError("source: missing 'connector'")
        sources.append(Source(
            connector=str(raw["connector"]), required=bool(raw.get("required", True)),
            freshness_hours=int(raw.get("freshness_hours", 48)),
            source_class=str(raw.get("class", "fetched")),
        ))
    return tuple(sources)


def _load_predicate(raw: Mapping[str, Any]) -> Predicate:
    _reject_unknown(raw, _PRED_KEYS, "predicate")
    name, ptype = raw.get("name"), raw.get("type")
    if not name or ptype not in _PREDICATE_TYPES:
        raise PackError(f"predicate {name!r}: type must be one of {_PREDICATE_TYPES}")
    values = tuple(str(v) for v in raw.get("values", ()))
    if ptype == "enum" and not values:
        raise PackError(f"predicate {name!r}: enum needs 'values'")
    if ptype != "enum" and values:
        raise PackError(f"predicate {name!r}: only enum predicates have 'values'")
    authority = tuple(str(a) for a in raw.get("authority", ()))
    if not authority:
        raise PackError(f"predicate {name!r}: authority per predicate is required")
    conf = Confidentiality(raw["confidentiality"]) if raw.get("confidentiality") else None
    return Predicate(
        name=name, type=ptype, authority=authority, values=values,
        high_stakes=bool(raw.get("high_stakes", False)), confidentiality=conf,
        lead_days=int(raw.get("lead_days", 0)),
    )


def _load_predicates(raw_predicates: Any) -> dict[str, Predicate]:
    predicates: dict[str, Predicate] = {}
    for raw in raw_predicates:
        predicate = _load_predicate(raw)
        if predicate.name in predicates:
            raise PackError(f"duplicate predicate {predicate.name!r}")
        predicates[predicate.name] = predicate
    return predicates


def _load_rules(raw_rules: Any, predicates: Mapping[str, Predicate]) -> tuple[AlertRule, ...]:
    rules: list[AlertRule] = []
    for raw in raw_rules:
        _reject_unknown(raw, _RULE_KEYS, "alert_rule")
        pred = raw.get("predicate")
        if pred not in predicates:
            raise PackError(f"alert rule references unknown predicate {pred!r}")
        try:
            severity = Severity(raw.get("severity"))
        except ValueError as exc:
            raise PackError(f"alert rule for {pred}: bad severity {raw.get('severity')!r}") from exc
        text = str(raw.get("when", ""))
        try:
            tree = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise PackError(f"alert rule for {pred}: cannot parse {text!r}") from exc
        _check_node(tree, set())
        _check_enum_literals(tree.body, predicates[pred])
        rules.append(AlertRule(pred, text, severity, tree))
    return tuple(rules)


def load_pack(data: Mapping[str, Any], bindings: Mapping[str, Any] | None = None) -> DomainPack:
    if not isinstance(data, Mapping):
        raise PackError("pack must be a mapping")
    _reject_unknown(data, _TOP_KEYS, "pack")
    for required in ("domain", "version", "confidentiality", "impact", "sources", "predicates"):
        if required not in data:
            raise PackError(f"pack: missing '{required}'")
    if not _SEMVER.match(str(data["version"])):
        raise PackError(f"version {data['version']!r} is not semver")
    try:
        confidentiality = Confidentiality(data["confidentiality"])
    except ValueError as exc:
        raise PackError(f"bad confidentiality {data['confidentiality']!r}") from exc

    merged: dict[str, Any] = {**(data.get("bindings") or {}), **(bindings or {})}
    predicates = _load_predicates(data["predicates"])

    arith: Mapping[str, Any] = data.get("deadline_arithmetic") or {}
    _reject_unknown(arith, _ARITH_KEYS, "deadline_arithmetic")
    tz, tz_name = _resolve_timezone(str(arith.get("timezone", "UTC")), merged)
    end_dates = str(arith.get("end_dates", "inclusive"))
    if end_dates not in ("exclusive", "inclusive"):
        raise PackError(f"end_dates must be exclusive or inclusive, got {end_dates!r}")

    return DomainPack(
        domain=str(data["domain"]), version=str(data["version"]), confidentiality=confidentiality,
        impact=str(data["impact"]), sources=_load_sources(data["sources"]), predicates=predicates,
        rules=_load_rules(data.get("alert_rules", ()), predicates), timezone=tz, timezone_name=tz_name,
        holidays=_holidays(str(arith.get("business_days", "weekdays")), merged), end_dates=end_dates,
        cross_domain_cases=tuple(str(c) for c in data.get("cross_domain_cases", ())),
        fixtures_dir=str(data.get("fixtures_dir", "")),
    )


def _check_enum_literals(node: ast.AST, pred: Predicate) -> None:
    """A rule comparing ``value`` to a literal must use a value the enum declares."""
    if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) and node.left.id == "value":
        for comparator in node.comparators:
            literals = [comparator] if isinstance(comparator, ast.Constant) else (
                list(comparator.elts) if isinstance(comparator, (ast.List, ast.Tuple)) else [])
            for lit in literals:
                if pred.type == "enum" and isinstance(lit, ast.Constant) and lit.value not in pred.values:
                    raise PackError(f"rule for {pred.name}: {lit.value!r} is not a declared value "
                                    f"{list(pred.values)}")
    for child in ast.iter_child_nodes(node):
        _check_enum_literals(child, pred)


def load_pack_file(path: Path | str, bindings: Mapping[str, Any] | None = None) -> DomainPack:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PackError(f"cannot read pack {path}: {exc}") from exc
    return load_pack(data, bindings)


def composite_pack(packs: Sequence[DomainPack]) -> DomainPack:
    """Compose multiple domain packs into a single combined pack."""
    if not packs:
        raise PackError("cannot compose empty sequence of packs")
    if len(packs) == 1:
        return packs[0]

    domain = "+".join(p.domain for p in packs)
    version = packs[0].version
    conf_order = {Confidentiality.ORDINARY: 0, Confidentiality.SENSITIVE: 1, Confidentiality.RESTRICTED: 2}
    confidentiality = max((p.confidentiality for p in packs), key=lambda c: conf_order.get(c, 0))

    impact_order = {"advisory": 0, "consequential": 1, "critical": 2}
    impact = max((p.impact for p in packs), key=lambda i: impact_order.get(i, 0))

    sources_by_connector: dict[str, Source] = {}
    for p in packs:
        for s in p.sources:
            if s.connector not in sources_by_connector or s.required:
                sources_by_connector[s.connector] = s
    sources = tuple(sources_by_connector.values())

    predicates: dict[str, Predicate] = {}
    for p in packs:
        for name, pred in p.predicates.items():
            if name in predicates and predicates[name] != pred:
                raise PackError(f"predicate conflict in composite pack: {name}")
            predicates[name] = pred

    rules: list[AlertRule] = []
    for p in packs:
        rules.extend(p.rules)

    holidays: set[date] = set()
    for p in packs:
        holidays.update(p.holidays)

    cross_cases: list[str] = []
    for p in packs:
        cross_cases.extend(p.cross_domain_cases)

    return DomainPack(
        domain=domain,
        version=version,
        confidentiality=confidentiality,
        impact=impact,
        sources=sources,
        predicates=predicates,
        rules=tuple(rules),
        timezone=packs[0].timezone,
        timezone_name=packs[0].timezone_name,
        holidays=frozenset(holidays),
        end_dates=packs[0].end_dates,
        cross_domain_cases=tuple(cross_cases),
        fixtures_dir="",
    )
