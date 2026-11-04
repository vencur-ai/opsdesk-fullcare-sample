"""Care-plan models: which fields a care-plan entry needs depends on the home (question Q4).

- "sis": the German structure model – the structured information collection (SIS) with its six themes, each with
  a finding, plus a measures plan (the measures). Theme names follow the public description of the model.
- "pgm": problem – resource – goal, then measures (UK homes: need – strengths – outcome – actions).

Measures are the same objects in both models (`Measure`), so the daily plan and the verifier do not care which
model the home uses.
"""

from __future__ import annotations

from typing import Literal

SIS_THEMES: dict[str, dict[str, str]] = {
    "cognition": {"de-DE": "Kognitive und kommunikative Fähigkeiten", "en-GB": "Thinking and communication"},
    "mobility": {"de-DE": "Mobilität und Beweglichkeit", "en-GB": "Mobility"},
    "health": {"de-DE": "Krankheitsbezogene Anforderungen und Belastungen", "en-GB": "Health needs"},
    "self_care": {"de-DE": "Selbstversorgung", "en-GB": "Self-care"},
    "social": {"de-DE": "Leben in sozialen Beziehungen", "en-GB": "Relationships"},
    "home": {"de-DE": "Wohnen und Häuslichkeit", "en-GB": "Living environment"},
}

FIELD_LABELS: dict[str, dict[str, str]] = {
    "theme": {"de-DE": "Themenfeld", "en-GB": "Theme"},
    "finding": {"de-DE": "Einschätzung", "en-GB": "Finding"},
    "problem": {"de-DE": "Problem", "en-GB": "Need"},
    "resource": {"de-DE": "Ressource", "en-GB": "Strengths"},
    "goal": {"de-DE": "Ziel", "en-GB": "Outcome"},
}

REQUIRED: dict[str, tuple[str, ...]] = {"sis": ("theme", "finding"), "pgm": ("problem", "goal")}


def entry_errors(model: Literal["sis", "pgm"], fields: dict[str, str]) -> list[str]:
    """What is missing or wrong for an entry in this home's model (empty list: fine)."""
    errors = [f"{name} is required" for name in REQUIRED[model] if not str(fields.get(name, "")).strip()]
    if model == "sis" and fields.get("theme") and fields["theme"] not in SIS_THEMES:
        errors.append("theme must be one of: " + ", ".join(SIS_THEMES))
    other = {"sis": ("problem", "resource", "goal"), "pgm": ("theme", "finding")}[model]
    errors += [f"{name} is not used in this home's care plan" for name in other if str(fields.get(name, "")).strip()]
    return errors
