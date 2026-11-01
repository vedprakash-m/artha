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
from typing import Any, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from catchup_core import yaml_cache
from catchup_core.contracts import Confidentiality, Severity

__all__ = [
    "RULE_VARIABLES",
    "AlertRule",
    "DomainPack",
    "EffectiveInterval",
    "PackCompatibility",
    "PackError",
    "PackTransition",
    "PermissionRule",
    "PolicyBinding",
    "Predicate",
    "QuantityDeclaration",
    "RetentionPolicy",
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
    "retention", "permissions", "compatibility", "migration", "rollback", "policy_bindings",
}
_PRED_KEYS = {"name", "type", "values", "high_stakes", "confidentiality", "authority", "lead_days", "event", "hint",
              "jurisdiction", "effective_interval", "quantity", "resolves", "lapse_days", "aliases", "unless"}
_SOURCE_KEYS = {"connector", "required", "freshness_hours", "class"}
_RULE_KEYS = {"predicate", "when", "severity"}
_ARITH_KEYS = {"timezone", "date_only", "end_dates", "business_days", "latest_useful_action"}
_POLICIES = {'retention', 'permissions', 'jurisdiction', 'effective_interval', 'quantity',
             'compatibility', 'migration', 'rollback', 'source_authority'}
_Declaration = TypeVar('_Declaration')


@dataclass(frozen=True)
class RetentionPolicy:
    """Maximum content and minimal-audit lifetimes; no pruning is performed here."""

    max_days: int
    audit_days: int


@dataclass(frozen=True)
class PermissionRule:
    """Exact scope and predicate selectors; deny overrides allow in the consuming policy."""

    scope: str
    predicates: tuple[str, ...]
    effect: str


@dataclass(frozen=True)
class EffectiveInterval:
    """Explicit inclusive start and exclusive end, without inferred open boundaries."""

    start: date
    end: date


@dataclass(frozen=True)
class QuantityDeclaration:
    """Metadata only: a parent typed-value consumer is required before evaluation."""

    unit: str
    currency: str | None = None


@dataclass(frozen=True)
class PackCompatibility:
    core_contract: str


@dataclass(frozen=True)
class PackTransition:
    """Declared compatibility, not permission to apply or reverse DDL."""

    from_version: str
    to_version: str
    compatible: bool


@dataclass(frozen=True)
class PolicyBinding:
    """Versioned policy reference; existence, approval and enforcement are runtime guards."""

    policy_id: str
    version: str


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
    hint: str = ""              # one line shown to the extractor so it knows what the predicate means
    event: bool = False          # a dated occurrence (test, appointment), not a deadline: once past it is not overdue
    jurisdiction: tuple[str, ...] | None = None
    effective_interval: EffectiveInterval | None = None
    quantity: QuantityDeclaration | None = None
    lapse_days: int | None = None  # days past the date after which nothing can still be done, so it is not surfaced
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (enum value, words a message may use to state it)
    unless: tuple[str, ...] = ()  # words that, in the cited sentence, stop any value of this enum from grounding
    resolves: str = ""          # a current fact of this predicate settles the named-obligation fact of that predicate

    @property
    def execution_supported(self) -> bool:
        return self.type in _PREDICATE_TYPES and self.quantity is None

    def applies(self, on: date, jurisdiction: str | None) -> bool | None:
        """Whether the predicate is in force on ``on`` in ``jurisdiction``; ``None`` when that cannot be known.

        The interval start is inclusive and its end exclusive.  A declared jurisdiction covers its own
        subdivisions (``US`` covers ``US-WA``) but not the reverse.  A definite ``False`` wins over unknown.
        """
        window = self.effective_interval
        if window is not None and not window.start <= on < window.end:
            return False
        if self.jurisdiction is None:
            return True
        if jurisdiction is None:
            return None
        return any(jurisdiction == name or jurisdiction.startswith(name + "-") for name in self.jurisdiction)

    def accepts_quantity(self, unit: str, currency: str | None) -> bool:
        """A quantity is accepted only when its unit and currency equal the declaration exactly."""
        return (self.quantity is not None and self.quantity.unit == unit
                and self.quantity.currency == currency)

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
    predicate_domains: Mapping[str, str] = field(default_factory=dict)
    predicate_packs: Mapping[str, DomainPack] = field(default_factory=dict, repr=False)
    retention: RetentionPolicy | None = None
    permissions: tuple[PermissionRule, ...] | None = None
    compatibility: PackCompatibility | None = None
    migration: PackTransition | None = None
    rollback: PackTransition | None = None
    policy_bindings: Mapping[str, PolicyBinding] = field(default_factory=dict)
    components: tuple[DomainPack, ...] = field(default=(), repr=False)

    @property
    def extension_requirements(self) -> tuple[str, ...]:
        """Missing contract declarations/bindings, never measured capability evidence."""
        if self.components:
            return tuple(sorted({f'{component.domain}:{requirement}'
                                 for component in self.components
                                 for requirement in component.extension_requirements}))
        missing = [name for name in ('retention', 'permissions', 'compatibility')
                   if getattr(self, name) is None]
        required = {'retention', 'permissions', 'compatibility', 'source_authority'}
        for name in ('migration', 'rollback'):
            if getattr(self, name) is not None:
                required.add(name)
        for pred in self.predicates.values():
            for name in ('jurisdiction', 'effective_interval'):
                if getattr(pred, name) is not None:
                    required.add(name)
                elif pred.high_stakes:
                    missing.append(f'{name}:{pred.name}')
            if pred.quantity is not None:
                required.add('quantity')
            if not pred.execution_supported:
                missing.append(f'quantity_consumer:{pred.name}')
        missing.extend(f'policy_binding:{name}' for name in sorted(required - self.policy_bindings.keys()))
        return tuple(missing)

    @property
    def extension_ready(self) -> bool:
        """Contract readiness only; does not certify enforcement, coverage or measurement."""
        return not self.extension_requirements

    @property
    def runtime_guards_required(self) -> tuple[str, ...]:
        return ('policy_bindings', 'permissions', 'retention', 'jurisdiction', 'effective_interval',
                'quantity_consumer', 'compatibility', 'migration', 'rollback', 'source_authority')

    def permission(self, scope: str, predicate: str) -> str | None:
        """``deny`` beats ``allow``; ``None`` means no rule matches, which the consuming policy treats as no access."""
        if predicate not in self.predicates:
            raise PackError(f'unknown predicate {predicate!r}')
        effects = {rule.effect for rule in self.permissions or ()
                   if rule.scope == scope and predicate in rule.predicates}
        return 'deny' if 'deny' in effects else 'allow' if 'allow' in effects else None

    def can_move(self, installed: str, target: str) -> bool | None:
        """Declared compatibility of a version move; ``None`` when this pack never declared that move."""
        if installed == target:
            return True
        for move in (self.migration, self.rollback):
            if move is not None and (move.from_version, move.to_version) == (installed, target):
                return move.compatible
        return None

    def for_predicate(self, predicate: str) -> DomainPack:
        """A composite retains the component's calendar, timezone, rules and source policy."""
        if predicate not in self.predicates:
            raise PackError(f'unknown predicate {predicate!r}')
        return self.predicate_packs.get(predicate, self)

    # ── rules ────────────────────────────────────────────────────────────
    def evaluate(self, predicate: str, variables: Mapping[str, Any]) -> RuleResult:
        """Most severe matching rule wins; unknown variables mark the result uncertain."""
        if predicate not in self.predicates:
            raise PackError(f"unknown predicate {predicate!r}")
        if not self.predicates[predicate].execution_supported:
            raise PackError(f'{predicate}: quantity execution requires a typed-value consumer')
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
        """``due`` minus lead time, using that predicate's business calendar."""
        component = self.for_predicate(predicate)
        lead = self.predicates[predicate].lead_days
        day = due
        remaining = lead
        while remaining > 0:
            day -= timedelta(days=1)
            if day.weekday() < 5 and day not in component.holidays:
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
    if not isinstance(mapping, Mapping):
        raise PackError(f'{where}: expected a mapping')
    extra = set(mapping) - allowed
    if extra:
        raise PackError(f"{where}: unknown key(s) {sorted(extra, key=str)}")


def _rows(value: Any, where: str) -> Sequence[Any]:
    if not isinstance(value, list | tuple):
        raise PackError(f'{where}: expected a list')
    return value


def _strings(value: Any, where: str) -> tuple[str, ...]:
    items = _rows(value, where)
    if any(not isinstance(v, str) or not v.strip() or len(v) > 256 for v in items):
        raise PackError(f'{where}: expected nonempty bounded strings')
    if len(set(items)) != len(items):
        raise PackError(f'{where}: duplicate values')
    return tuple(items)


def _identifier(value: Any, where: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,127}', value):
        raise PackError(f'{where}: expected a nonempty identifier')
    return value


def _boolean(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise PackError(f'{key}: expected a boolean')
    return value


def _integer(raw: Mapping[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value = raw.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise PackError(f'{key}: expected an integer between {minimum} and {maximum}')
    return int(value)


def _required_mapping(value: Any, keys: set[str], where: str) -> Mapping[str, Any]:
    _reject_unknown(value, keys, where)
    if set(value) != keys:
        raise PackError(f'{where}: required keys are {sorted(keys)}')
    result: Mapping[str, Any] = value
    return result


def _version(value: Any, where: str) -> str:
    semver = r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)'
    if not isinstance(value, str) or not re.fullmatch(semver, value):
        raise PackError(f'{where}: expected semver')
    return value


def _retention(value: Any) -> RetentionPolicy:
    raw = _required_mapping(value, {'max_days', 'audit_days'}, 'retention')
    return RetentionPolicy(_integer(raw, 'max_days', 0, 1, 36500),
                           _integer(raw, 'audit_days', 0, 0, 36500))


def _permissions(value: Any, predicates: Mapping[str, Predicate]) -> tuple[PermissionRule, ...]:
    rules: list[PermissionRule] = []
    for item in _rows(value, 'permissions'):
        raw = _required_mapping(item, {'scope', 'predicates', 'effect'}, 'permission')
        scope = _identifier(raw['scope'], 'permission scope')
        names = _strings(raw['predicates'], 'permission predicates')
        if not names or set(names) - predicates.keys():
            raise PackError('permission predicates must select declared predicates')
        effect = raw['effect']
        if not isinstance(effect, str) or effect not in {'allow', 'deny'}:
            raise PackError('permission effect must be allow or deny')
        rule = PermissionRule(scope, names, effect)
        if rule in rules:
            raise PackError('duplicate permission rule')
        rules.append(rule)
    if not rules:
        raise PackError('permissions must not be empty')
    return tuple(rules)


def _interval(value: Any) -> EffectiveInterval:
    raw = _required_mapping(value, {'start', 'end'}, 'effective_interval')
    days: list[date] = []
    for key in ('start', 'end'):
        val = raw[key]
        if isinstance(val, date) and not isinstance(val, datetime):
            days.append(val)
            continue
        if not isinstance(val, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', val):
            raise PackError('effective_interval requires YYYY-MM-DD literals')
        try:
            days.append(date.fromisoformat(val))
        except ValueError as exc:
            raise PackError('effective_interval: invalid date') from exc
    if days[0] >= days[1]:
        raise PackError('effective_interval start must precede end')
    return EffectiveInterval(days[0], days[1])


def _jurisdiction(value: Any) -> tuple[str, ...]:
    names = _strings(value, 'jurisdiction')
    if not names or any(not re.fullmatch(r'[A-Z]{2}(?:-[A-Z0-9]{1,8})*', name) for name in names):
        raise PackError('jurisdiction requires explicit country/subdivision literals')
    return names


def _quantity(value: Any) -> QuantityDeclaration:
    _reject_unknown(value, {'unit', 'currency'}, 'quantity')
    unit = _identifier(value.get('unit'), 'quantity unit')
    currency = value.get('currency')
    if 'currency' in value and (not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency)):
        raise PackError('quantity currency must be a three-uppercase-letter literal')
    return QuantityDeclaration(unit, currency)


def _transition(value: Any, version: str, name: str) -> PackTransition:
    raw = _required_mapping(value, {'from_version', 'to_version', 'compatible'}, name)
    before = _version(raw['from_version'], name)
    after = _version(raw['to_version'], name)
    if before == after or (after if name == 'migration' else before) != version:
        raise PackError(f'{name}: transition must connect the current pack version')
    return PackTransition(before, after, _boolean(raw, 'compatible', False))


def _policy_bindings(value: Any) -> dict[str, PolicyBinding]:
    _reject_unknown(value, _POLICIES, 'policy_bindings')
    bindings: dict[str, PolicyBinding] = {}
    for name, item in value.items():
        raw = _required_mapping(item, {'policy_id', 'version'}, 'policy binding')
        bindings[name] = PolicyBinding(_identifier(raw['policy_id'], 'policy_id'),
                                       _version(raw['version'], 'policy version'))
    return bindings


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
        return frozenset(date.fromisoformat(str(d)) for d in _rows(bound["holidays"], 'holidays'))
    except ValueError as exc:
        raise PackError(f"business_days {ref!r}: bad holiday date") from exc


def _load_sources(raw_sources: Any) -> tuple[Source, ...]:
    sources: list[Source] = []
    for raw in _rows(raw_sources, 'sources'):
        _reject_unknown(raw, _SOURCE_KEYS, "source")
        if "connector" not in raw:
            raise PackError("source: missing 'connector'")
        connector = _identifier(raw['connector'], 'source connector')
        if any(s.connector == connector for s in sources):
            raise PackError(f'duplicate source connector {connector!r}')
        sources.append(Source(
            connector=connector, required=_boolean(raw, 'required', True),
            freshness_hours=_integer(raw, 'freshness_hours', 48, 1, 8760),
            source_class=_identifier(raw.get('class', 'fetched'), 'source class'),
        ))
    return tuple(sources)


def _aliases(raw: Any, values: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(raw, Mapping):
        raise PackError("predicate aliases must map an enum value to a list of words")
    out = []
    for value, words in raw.items():
        if value not in values or not isinstance(words, list) or not words \
                or not all(isinstance(w, str) and w.strip() for w in words):
            raise PackError(f"predicate aliases: {value!r} must be a value of the predicate with a list of words")
        out.append((value, tuple(w.strip() for w in words)))
    return tuple(out)


def _unless(raw: Any, ptype: str) -> tuple[str, ...]:
    if ptype != "enum" or not isinstance(raw, list) or not raw \
            or not all(isinstance(w, str) and w.strip() for w in raw):
        raise PackError("predicate unless must be a list of words on an enum predicate")
    return tuple(w.strip() for w in raw)


def _load_predicate(raw: Mapping[str, Any]) -> Predicate:
    _reject_unknown(raw, _PRED_KEYS, "predicate")
    name, ptype = raw.get("name"), raw.get("type")
    if not name or ptype not in _PREDICATE_TYPES:
        raise PackError(f"predicate {name!r}: type must be one of {_PREDICATE_TYPES}")
    name = _identifier(name, 'predicate name')
    values = _strings(raw.get("values", ()), 'predicate values')
    if ptype == "enum" and not values:
        raise PackError(f"predicate {name!r}: enum needs 'values'")
    if ptype != "enum" and values:
        raise PackError(f"predicate {name!r}: only enum predicates have 'values'")
    authority = _strings(raw.get("authority", ()), 'predicate authority')
    if not authority:
        raise PackError(f"predicate {name!r}: authority per predicate is required")
    try:
        conf = Confidentiality(raw['confidentiality']) if raw.get('confidentiality') is not None else None
    except ValueError as exc:
        raise PackError('predicate confidentiality is invalid') from exc
    hint = raw.get('hint', '')
    if not isinstance(hint, str) or len(hint) > 160:
        raise PackError('hint must be text of at most 160 characters')
    return Predicate(
        name=name, type=ptype, authority=authority, values=values,
        high_stakes=_boolean(raw, 'high_stakes', False), confidentiality=conf,
        lead_days=_integer(raw, 'lead_days', 0, 0, 3660), event=_boolean(raw, 'event', False),
        hint=hint,
        jurisdiction=_jurisdiction(raw['jurisdiction']) if 'jurisdiction' in raw else None,
        effective_interval=_interval(raw['effective_interval']) if 'effective_interval' in raw else None,
        quantity=_quantity(raw['quantity']) if 'quantity' in raw else None,
        lapse_days=_integer(raw, 'lapse_days', 0, 0, 3660) if 'lapse_days' in raw else None,
        aliases=_aliases(raw['aliases'], values) if 'aliases' in raw else (),
        unless=_unless(raw['unless'], ptype) if 'unless' in raw else (),
        resolves=_identifier(raw['resolves'], 'predicate resolves') if raw.get('resolves') else '',
    )


def _load_predicates(raw_predicates: Any) -> dict[str, Predicate]:
    predicates: dict[str, Predicate] = {}
    for raw in _rows(raw_predicates, 'predicates'):
        predicate = _load_predicate(raw)
        if predicate.name in predicates:
            raise PackError(f"duplicate predicate {predicate.name!r}")
        predicates[predicate.name] = predicate
    for predicate in predicates.values():
        if predicate.resolves and (predicate.resolves == predicate.name or predicate.resolves not in predicates):
            raise PackError(f"predicate {predicate.name!r}: resolves must name another predicate of the pack")
    return predicates


def _load_rules(raw_rules: Any, predicates: Mapping[str, Predicate]) -> tuple[AlertRule, ...]:
    rules: list[AlertRule] = []
    for raw in _rows(raw_rules, 'alert rules'):
        _reject_unknown(raw, _RULE_KEYS, "alert_rule")
        pred = raw.get("predicate")
        if pred not in predicates:
            raise PackError(f"alert rule references unknown predicate {pred!r}")
        try:
            severity = Severity(raw.get("severity"))
        except ValueError as exc:
            raise PackError(f"alert rule for {pred}: bad severity {raw.get('severity')!r}") from exc
        text = raw.get('when', '')
        if not isinstance(text, str):
            raise PackError('alert rule when must be text')
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
    if not isinstance(data['version'], str) or not _SEMVER.fullmatch(data['version']):
        raise PackError(f"version {data['version']!r} is not semver")
    _identifier(data['domain'], 'domain')
    if not isinstance(data['impact'], str) or data['impact'] not in {'advisory', 'consequential', 'critical'}:
        raise PackError('impact must be advisory, consequential or critical')
    if (not isinstance(data.get('bindings', {}), Mapping)
            or (bindings is not None and not isinstance(bindings, Mapping))):
        raise PackError('bindings must be a mapping')
    if not isinstance(data.get('fixtures_dir', ''), str):
        raise PackError('fixtures_dir must be text')
    try:
        confidentiality = Confidentiality(data["confidentiality"])
    except ValueError as exc:
        raise PackError(f"bad confidentiality {data['confidentiality']!r}") from exc

    merged: dict[str, Any] = {**(data.get("bindings") or {}), **(bindings or {})}
    predicates = _load_predicates(data["predicates"])

    arith: Mapping[str, Any] = data.get("deadline_arithmetic", {})
    _reject_unknown(arith, _ARITH_KEYS, "deadline_arithmetic")
    tz, tz_name = _resolve_timezone(str(arith.get("timezone", "UTC")), merged)
    end_dates = str(arith.get("end_dates", "inclusive"))
    if end_dates not in ("exclusive", "inclusive"):
        raise PackError(f"end_dates must be exclusive or inclusive, got {end_dates!r}")

    compatibility = None
    if 'compatibility' in data:
        raw_compat = _required_mapping(data['compatibility'], {'core_contract'}, 'compatibility')
        compatibility = PackCompatibility(_version(raw_compat['core_contract'], 'core_contract'))
    migration = _transition(data['migration'], data['version'], 'migration') if 'migration' in data else None
    rollback = _transition(data['rollback'], data['version'], 'rollback') if 'rollback' in data else None
    if rollback is not None and (migration is None or rollback.to_version != migration.from_version):
        raise PackError('rollback must reverse the declared migration versions')

    return DomainPack(
        domain=str(data["domain"]), version=str(data["version"]), confidentiality=confidentiality,
        impact=str(data["impact"]), sources=_load_sources(data["sources"]), predicates=predicates,
        rules=_load_rules(data.get("alert_rules", ()), predicates), timezone=tz, timezone_name=tz_name,
        holidays=_holidays(str(arith.get("business_days", "weekdays")), merged), end_dates=end_dates,
        cross_domain_cases=_strings(data.get("cross_domain_cases", ()), 'cross-Domain cases'),
        fixtures_dir=str(data.get("fixtures_dir", "")),
        predicate_domains={name: str(data["domain"]) for name in predicates},
        retention=_retention(data['retention']) if 'retention' in data else None,
        permissions=_permissions(data['permissions'], predicates) if 'permissions' in data else None,
        compatibility=compatibility, migration=migration, rollback=rollback,
        policy_bindings=_policy_bindings(data.get('policy_bindings', {})),
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
        data = yaml_cache.safe_load(Path(path).read_text(encoding="utf-8"))
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
    if len({p.domain for p in packs}) != len(packs):
        raise PackError('duplicate Domain ownership in composite pack')
    version = '+'.join(f'{p.domain}@{p.version}' for p in packs)
    conf_order = {Confidentiality.ORDINARY: 0, Confidentiality.SENSITIVE: 1, Confidentiality.RESTRICTED: 2}
    confidentiality = max((p.confidentiality for p in packs), key=lambda c: conf_order.get(c, 0))

    impact_order = {"advisory": 0, "consequential": 1, "critical": 2}
    impact = max((p.impact for p in packs), key=lambda i: impact_order.get(i, 0))

    sources_by_connector: dict[str, Source] = {}
    for p in packs:
        for s in p.sources:
            previous = sources_by_connector.get(s.connector)
            if previous is not None and previous.source_class != s.source_class:
                raise PackError(f'conflicting source class for {s.connector!r}')
            sources_by_connector[s.connector] = Source(s.connector,
                s.required or bool(previous and previous.required),
                min(s.freshness_hours, previous.freshness_hours) if previous else s.freshness_hours, s.source_class)
    sources = tuple(sources_by_connector.values())

    predicates: dict[str, Predicate] = {}
    for p in packs:
        for name, pred in p.predicates.items():
            if name in predicates:
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

    retention_policies = [p.retention for p in packs if p.retention is not None]
    retention = (RetentionPolicy(min(p.max_days for p in retention_policies),
                                 min(p.audit_days for p in retention_policies)) if retention_policies else None)
    permissions = tuple(rule for p in packs for rule in (p.permissions or ())) or None
    compatibility = _consistent([p.compatibility for p in packs], 'core contract versions')
    migration = _consistent([p.migration for p in packs], 'migration versions or compatibility')
    rollback = _consistent([p.rollback for p in packs], 'rollback versions or compatibility')
    _consistent([pred.quantity for pred in predicates.values()], 'quantity units or currencies')

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
        predicate_domains={name: p.predicate_domains.get(name, p.domain)
                           for p in packs for name in p.predicates},
        predicate_packs={name: p.for_predicate(name) for p in packs for name in p.predicates},
        retention=retention, permissions=permissions, compatibility=compatibility,
        migration=migration, rollback=rollback, components=tuple(packs),
    )


def _consistent(values: Sequence[_Declaration | None], where: str) -> _Declaration | None:
    declared = [value for value in values if value is not None]
    if declared and any(value != declared[0] for value in declared[1:]):
        raise PackError(f'conflicting {where} in composite pack')
    return declared[0] if declared else None
