"""Prompt templates for fixture generation."""
from __future__ import annotations

from collections.abc import Callable

from eval.fixtures.schema import CEFRBand

# Canonical error type vocabulary. All prompts and validation must use these
# exact strings for errors_present, errors_observed, and priority_error fields.
ERROR_TYPES_BY_BAND: dict[CEFRBand, list[str]] = {
    "A1": ["gender_agreement", "missing_article", "verb_conjugation", "ser_estar"],
    "A2": ["gender_agreement", "ser_estar", "preterite_imperfect", "verb_conjugation"],
    "B1": ["ser_estar", "preterite_imperfect", "subjunctive_avoidance", "por_para"],
    "B2": ["subjunctive", "por_para", "register_slip", "idiomatic_error"],
    "C1": ["subjunctive", "register_slip", "idiomatic_error"],
}

ALL_ERROR_TYPES = sorted({t for types in ERROR_TYPES_BY_BAND.values() for t in types})

from . import (
    cold_start,
    error_pattern_threshold,
    l1_handling,
    mimicry_cycle,
    multi_error,
    register_boundary,
    single_error_recast,
    tool_call_correctness,
)

BuildPrompts = Callable[[int, CEFRBand], list[str]]

CATEGORIES: dict[str, BuildPrompts] = {
    "single_error_recast": single_error_recast.build_user_prompts,
    "multi_error": multi_error.build_user_prompts,
    "l1_handling": l1_handling.build_user_prompts,
    "mimicry_cycle": mimicry_cycle.build_user_prompts,
    "cold_start": cold_start.build_user_prompts,
    "register_boundary": register_boundary.build_user_prompts,
    "tool_call_correctness": tool_call_correctness.build_user_prompts,
    "error_pattern_threshold": error_pattern_threshold.build_user_prompts,
}

# Spec §"Target counts". cold_start B1 column is 4 per band in the text.
TARGET_COUNTS: dict[str, dict[CEFRBand, int]] = {
    "single_error_recast":   {"A1": 10, "A2": 10, "B1": 10, "B2": 10, "C1": 10},
    "multi_error":           {"A1": 6,  "A2": 6,  "B1": 6,  "B2": 6,  "C1": 6},
    "l1_handling":           {"A1": 5,  "A2": 5,  "B1": 5,  "B2": 5,  "C1": 5},
    "mimicry_cycle":         {"A1": 15},
    "cold_start":            {"A1": 4,  "A2": 4,  "B1": 4,  "B2": 4,  "C1": 4},
    "register_boundary":     {"A1": 4,  "A2": 4,  "B1": 4,  "B2": 4,  "C1": 4},
    "tool_call_correctness": {"A1": 4,  "A2": 4,  "B1": 4,  "B2": 4,  "C1": 4},
    "error_pattern_threshold": {"A1": 4, "A2": 4, "B1": 4, "B2": 4, "C1": 4},
}
