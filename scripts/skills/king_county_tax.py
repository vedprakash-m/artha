"""
king_county_tax.py — Backward-compatibility shim.

All logic has moved to property_tax.py (PropertyTaxSkill / get_skill).
This module re-exports KingCountyTaxSkill and get_skill so that existing
config/skills.yaml entries referencing "king_county_tax" continue to work
without any changes.

Ref: standardization.md §7.5.3, T-2.2.x
"""

from .property_tax import PropertyTaxSkill as KingCountyTaxSkill, get_skill

__all__ = ["KingCountyTaxSkill", "get_skill"]
