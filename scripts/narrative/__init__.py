"""
scripts/narrative/__init__.py — NarrativeEngine facade + CLI entry point.

Imports NarrativeEngineBase and re-exports NarrativeEngine (the full
concrete class with all template methods) so callers can do:

    from narrative import NarrativeEngine
    engine = NarrativeEngine(state_dir=...)
    result = engine.generate_weekly_memo()

The ``main()`` function at the bottom provides the CLI entry point used
by scripts/narrative_engine.py (the backward-compatible facade).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from narrative._base import NarrativeEngineBase, _WORK_STATE_DIR


class NarrativeEngine(NarrativeEngineBase):
    """Concrete NarrativeEngine — delegates all template generation to submodules."""

    def generate_weekly_memo(self, period: Optional[str] = None) -> str:
        from .memo import generate_weekly_memo
        return generate_weekly_memo(self, period)

    def generate_talking_points(self, topic: str) -> str:
        from .support import generate_talking_points
        return generate_talking_points(self, topic)

    def generate_boundary_report(self) -> str:
        from .support import generate_boundary_report
        return generate_boundary_report(self)

    def generate_connect_summary(self) -> str:
        from .connect import generate_connect_summary
        return generate_connect_summary(self)

    def generate_calibration_brief(self) -> str:
        from .connect import generate_calibration_brief
        return generate_calibration_brief(self)

    def generate_newsletter(self, period: Optional[str] = None) -> str:
        from .content import generate_newsletter
        return generate_newsletter(self, period)

    def generate_deck(self, topic: str = "") -> str:
        from .content import generate_deck
        return generate_deck(self, topic)

    def generate_promo_case(self, narrative: bool = False) -> str:
        from .career import generate_promo_case
        return generate_promo_case(self, narrative)

    def generate_escalation_memo(self, context: str) -> str:
        from .memo import generate_escalation_memo
        return generate_escalation_memo(self, context)

    def generate_decision_memo(self, decision_id: str = "") -> str:
        from .memo import generate_decision_memo
        return generate_decision_memo(self, decision_id)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Artha Work OS Narrative Engine — generate draft narratives from state files"
    )
    parser.add_argument(
        "--template",
        choices=[
            "weekly_memo", "talking_points", "boundary_report", "connect_summary",
            "newsletter", "deck", "promo_case", "promo_narrative",
            "calibration_brief", "escalation_memo", "decision_memo",
        ],
        required=True,
        help="Narrative template to generate",
    )
    parser.add_argument(
        "--topic",
        default="",
        help="Topic for talking_points or deck templates",
    )
    parser.add_argument(
        "--period",
        default="",
        help=(
            "Period label for weekly_memo or newsletter templates "
            "(e.g. 'Week of March 25, 2026')"
        ),
    )
    parser.add_argument(
        "--context",
        default="",
        help="Context description for escalation_memo template",
    )
    parser.add_argument(
        "--decision-id",
        default="",
        dest="decision_id",
        help="Decision ID (D-NNN) for decision_memo template",
    )
    parser.add_argument(
        "--state-dir",
        default=str(_WORK_STATE_DIR),
        help="Path to state/work/ directory",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Write output to this file path (default: stdout)",
    )
    args = parser.parse_args(argv)

    engine = NarrativeEngine(state_dir=Path(args.state_dir))

    if args.template == "weekly_memo":
        result = engine.generate_weekly_memo(period=args.period or None)
    elif args.template == "talking_points":
        if not args.topic:
            parser.error("--topic is required for talking_points template")
        result = engine.generate_talking_points(topic=args.topic)
    elif args.template == "boundary_report":
        result = engine.generate_boundary_report()
    elif args.template == "connect_summary":
        result = engine.generate_connect_summary()
    elif args.template == "newsletter":
        result = engine.generate_newsletter(period=args.period or None)
    elif args.template == "deck":
        result = engine.generate_deck(topic=args.topic)
    elif args.template == "promo_case":
        result = engine.generate_promo_case(narrative=False)
    elif args.template == "promo_narrative":
        result = engine.generate_promo_case(narrative=True)
    elif args.template == "calibration_brief":
        result = engine.generate_calibration_brief()
    elif args.template == "escalation_memo":
        if not args.context:
            parser.error("--context is required for escalation_memo template")
        result = engine.generate_escalation_memo(context=args.context)
    elif args.template == "decision_memo":
        result = engine.generate_decision_memo(decision_id=args.decision_id)
    else:
        result = "Unknown template"

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(
            f"[narrative_engine] wrote {len(result)} chars to {args.output}",
            file=sys.stderr,
        )
    else:
        print(result)


if __name__ == "__main__":
    main()
