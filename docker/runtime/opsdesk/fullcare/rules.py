"""House-standard rules engine: what the home's standard requires for a resident, as pure functions.

Used by the generator (seeded care plans follow the home's rules), by the task generator (references) and by the
verifier. A measure is compared by its *signature*: catalogue key and how often it happens, not the exact clock
times the nurse picked (the standard does not fix them), except where an order fixes them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from opsdesk.fullcare.models import Measure, Schedule
from opsdesk.fullcare.standards import HouseStandard, PreventionRule

Signature = tuple


def required_rules(standard: HouseStandard, classes: dict[str, str], *, base: bool) -> list[PreventionRule]:
    """Rules that apply for these risk classes (risk kind -> class); `base` adds the rules every resident has."""
    out = []
    for rule in standard.prevention:
        if rule.risk_kind == "base":
            if base:
                out.append(rule)
        elif classes.get(rule.risk_kind) in rule.classes:
            out.append(rule)
    return out


def rule_schedule(standard: HouseStandard, rule: PreventionRule, classes: dict[str, str]) -> Schedule:
    """The schedule a gold plan uses for a rule (default times; repositioning from the home's interval table)."""
    if rule.kind == "interval":
        rep = standard.reposition[classes.get("pressure_ulcer", "high")]
        return Schedule(kind="interval", every_hours=rep.every_hours, night_every_hours=rep.night_every_hours, anchor="06:00")
    if rule.kind == "weekly":
        return Schedule(kind="weekly", weekdays=list(rule.weekdays), times=rule.default_times[:1])
    return Schedule(kind="times", times=rule.default_times[:rule.per_day])


def rule_signature(standard: HouseStandard, rule: PreventionRule, classes: dict[str, str]) -> Signature:
    return schedule_signature(rule.catalogue_key, rule_schedule(standard, rule, classes))


def schedule_signature(key: str, schedule: Schedule) -> Signature:
    if schedule.kind == "interval":
        return (key, "interval", schedule.every_hours, schedule.night_every_hours or schedule.every_hours)
    if schedule.kind == "weekly":
        return (key, "weekly", len(schedule.weekdays), len(schedule.times))
    return (key, "per_day", len(schedule.times))


def exact_signature(key: str, schedule: Schedule) -> Signature:
    """For measures from an order: the ordered times are part of what must match."""
    return schedule_signature(key, schedule) + (tuple(sorted(schedule.times)), tuple(sorted(schedule.weekdays)))


def measure_signature(m: Measure) -> Signature:
    return schedule_signature(m.catalogue_key, m.schedule)


def active_on(m: Measure, day) -> bool:
    return m.start <= day and (m.end is None or day <= m.end)


def follow_up_due(measured_at: str, minutes: int) -> str:
    moment = datetime.strptime(measured_at, "%Y-%m-%d %H:%M") + timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%d %H:%M")
