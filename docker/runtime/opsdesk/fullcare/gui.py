"""Screens of the care record and the daily plan, on the Workforce page shell (server-rendered, no JavaScript).

Each view returns (title, area, body, side) like the Workforce office apps. Reads use the same tools or pure
functions the tools use; every change is one form posted to a route that calls `CareSession.step`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from opsdesk.fullcare import careplan, dailyplan, scales
from opsdesk.fullcare.i18n import L, ct
from opsdesk.fullcare.tools import UNITS, age, current_shift, latest_assessments
from opsdesk.workforce.gui.html import (
    badge, button, dialog, e, form, hidden, kv_table, page_number, paginate, pager, select, table, text_input,
    textarea, url,
)
from opsdesk.workforce.i18n import fmt_date, fmt_short

TABS = ("overview", "contacts", "diagnoses", "vitals", "fluids", "excretion", "assessments", "risks", "careplan",
        "report", "orders", "medication", "appointments", "wounds", "documents", "transfer")
OBS_KINDS = ("bp", "pulse", "temperature", "spo2", "glucose", "weight", "respiration", "pain", "height")


def _loc(ws) -> str:
    return ws.app.locale


def _stamp(loc: str, stamp: str) -> str:
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return e(stamp)
    return f"{e(fmt_short(loc, moment.date()))} {moment:%H:%M}"


def _day(loc: str, d: date | str | None) -> str:
    if d is None or d == "":
        return "–"
    return e(fmt_short(loc, date.fromisoformat(d) if isinstance(d, str) else d))


def _valid(loc: str, start, end) -> str:
    return f"{_day(loc, start)} – {_day(loc, end)}" if end else f"{e(ct(loc, 'from'))} {_day(loc, start)}"


def _unit_name(ws, ward_id: str) -> str:
    return ws.app.unit_names.get(ward_id, ward_id)


def _initials(name: str) -> str:
    """The hand sign (Handzeichen) shown next to an entry: the initials of who made it."""
    return "".join(part[0] for part in name.replace("-", " ").split() if part[:1].isalpha()).upper()


def _staff_name(ws, employee_id: str | None) -> str:
    if not employee_id:
        return ""
    return next((x.name for x in ws.state.employees if x.employee_id == employee_id), employee_id)


def _side(ws, current: str) -> str:
    loc = _loc(ws)
    links = (("residents", "/care/residents"), ("admit", "/care/admit"), ("orders", "/care/orders"),
             ("rounds", "/care/rounds"), ("print", "/print"), ("escalate", "/care/escalate"), ("handover", "/care/handover"),
             ("standard", "/care/standard"))
    items = "".join(f'<li><a href="{href}"{" aria-current=\"page\" class=\"active\"" if key == current else ""}>'
                    f'{e(ct(loc, "nav." + key))}</a></li>' for key, href in links)
    units = "".join(f'<li><a href="{e(url("/care/residents", ward=w.ward_id))}">{e(_unit_name(ws, w.ward_id))}</a></li>'
                    for w in ws.state.wards)
    return f'<ul>{items}</ul><h4>{e(ct(loc, "unit"))}</h4><ul>{units}</ul>'


# ---------------------------------------------------------------- residents list

def residents(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care, today = _loc(ws), ws.care, ws.state.current_date
    ward, text, status = q.get("ward", ""), q.get("q", "").strip(), q.get("status", "present")
    rows = []
    for r in sorted(care.residents, key=lambda x: (x.ward_id, x.room)):
        if ward and r.ward_id != ward or status != "all" and r.status != status:
            continue
        if text and text.lower() not in r.name.lower():
            continue
        points = []
        n = sum(1 for o in care.orders if o.resident_id == r.resident_id and o.acknowledged_by is None)
        if n:
            points.append(badge(f"{n} {ct(loc, 'to_ack')}", "open"))
        for a in latest_assessments(care, r.resident_id).values():
            if a.next_due is not None and a.next_due <= today:
                points.append(badge(f"{ct(loc, 'assessment_due')}: {scales.get_scale(a.scale_id).title[loc]}", "warn"))
        level = str(r.care_level) if r.care_level is not None else e(r.funding_note)
        rows.append([e(r.room), f'<a href="/care/residents/{e(r.resident_id)}">{e(r.family_name)}, {e(r.given_name)}</a>',
                     str(age(r.birth_date, today)), e(_unit_name(ws, r.ward_id)), e(ct(loc, "status." + r.status)),
                     level, " ".join(points)])
    shown, page_no, pages = paginate(rows, page_number(q))
    filters = form("/care/residents", text_input("q", ct(loc, "name"), text) +
                   select("ward", ct(loc, "unit"), [("", ct(loc, "all"))] + [(w.ward_id, _unit_name(ws, w.ward_id))
                                                                              for w in ws.state.wards], ward) +
                   select("status", ct(loc, "status"), [(s, ct(loc, "status." + s)) for s in
                                                         ("present", "in_hospital", "on_leave", "moved_out")]
                          + [("all", ct(loc, "all"))], status) + button(ct(loc, "search"), kind="secondary"),
                   method="get", cls="form inline")
    level_head = ct(loc, "care_level") if care.edition == "de" else ct(loc, "funding")
    body = filters + (table([ct(loc, "room"), ct(loc, "name"), ct(loc, "age"), ct(loc, "unit"), ct(loc, "status"),
                             level_head, ct(loc, "open_points")], shown) if shown else f"<p>{e(ct(loc, 'nothing'))}</p>")
    body += pager(loc, "/care/residents", page_no, pages, len(rows), q=text, ward=ward, status=status)
    return ct(loc, "nav.residents"), "care", body, _side(ws, "residents")


# ---------------------------------------------------------------- resident record

def record(ws, resident_id: str, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care, today = _loc(ws), ws.care, ws.state.current_date
    r = care.resident(resident_id)
    if r is None:
        return ct(loc, "nav.residents"), "care", f"<p>{e(ct(loc, 'nothing'))}</p>", _side(ws, "residents")
    tab = q.get("tab", "overview") if q.get("tab", "overview") in TABS else "overview"
    allergies = [a.substance for a in care.allergies if a.resident_id == resident_id]
    rep = next((c for c in care.contacts if c.resident_id == resident_id and c.representation != "none"), None)
    band = (f'<section class="band" aria-label="{e(r.name)}"><strong>{e(r.name)}</strong> · {e(ct(loc, "room"))} '
            f'{e(r.room)} · {e(_unit_name(ws, r.ward_id))} · {age(r.birth_date, today)} · '
            f'{e(ct(loc, "status." + r.status))}<br>{e(ct(loc, "allergies"))}: '
            f'<strong>{e(", ".join(allergies) if allergies else ct(loc, "no_allergies"))}</strong> · '
            f'{e(ct(loc, "representative"))}: {e(rep.name + " (" + ct(loc, "rep." + rep.representation) + ")") if rep else e(ct(loc, "none"))}'
            f'{" · " + e(ct(loc, "advance_directive")) if r.advance_directive else ""}</section>')
    tabs = "".join(f'<a href="{e(url("/care/residents/" + resident_id, tab=t))}"'
                   f'{" aria-current=\"page\" class=\"active\"" if t == tab else ""}>{e(ct(loc, "tab." + t))}</a>'
                   for t in TABS)
    body = band + f'<nav class="tabs" aria-label="{e(ct(loc, "tab.overview"))}">{tabs}</nav>'
    body += globals()["_tab_" + tab](ws, r, q)
    return r.name, "care", body, _side(ws, "residents")


def _back(rid: str, tab: str) -> str:
    return hidden(back=f"/care/residents/{rid}?tab={tab}", resident_id=rid)


def _tab_overview(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    gp = care.physician(r.gp_id)
    rows = [(e(ct(loc, "born")), _day(loc, r.birth_date)), (e(ct(loc, "birth_place")), e(r.birth_place)),
            (e(ct(loc, "admitted")), _day(loc, r.admitted_on)),
            (e(ct(loc, "care_level") if care.edition == "de" else ct(loc, "funding")),
             e(r.care_level if r.care_level is not None else r.funding_note)),
            (e(ct(loc, "gp")), e(f"{gp.name}, {gp.practice}" if gp else "–")),
            (e(ct(loc, "advance_directive")), e(ct(loc, "yes" if r.advance_directive else "no")))]
    risks = [ct(loc, "rk." + x.kind) for x in care.risks if x.resident_id == r.resident_id and x.present]
    rows.append((e(ct(loc, "tab.risks")), e(", ".join(sorted(risks)) or ct(loc, "none"))))
    statuses = [(x, ct(loc, "status." + x)) for x in ("present", "in_hospital", "on_leave") if x != r.status]
    change = form("/care/status", _back(r.resident_id, "overview") + select("status", ct(loc, "status"), statuses) +
                  text_input("reason", ct(loc, "reason"), required=True) + button(ct(loc, "set_status_res"), kind="secondary"),
                  cls="form inline")
    return kv_table(rows) + f"<h2>{e(ct(loc, 'set_status_res'))}</h2>" + change


def _tab_contacts(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    rows = [[e(c.name), e(c.relation), e(c.phone), e(c.mail), e(ct(loc, "yes" if c.emergency else "no")),
             e(ct(loc, "rep." + c.representation)) + (f' ({e(", ".join(c.representation_scope))})' if c.representation_scope else ""),
             e(ct(loc, "yes" if c.information_allowed else "no"))]
            for c in care.contacts if c.resident_id == r.resident_id]
    cons = [[e(ct(loc, "cons." + c.kind) if "cons." + c.kind in L else c.kind),
             e(next((x.name for x in care.contacts if x.contact_id == c.contact_id), "")), e(c.granted_by),
             _day(loc, c.granted_on)] for c in care.consents if c.resident_id == r.resident_id]
    out = table([ct(loc, "name"), ct(loc, "relation"), ct(loc, "phone"), ct(loc, "mail"), ct(loc, "emergency"),
                 ct(loc, "representative"), ct(loc, "information")], rows) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    out += f"<h2>{e(ct(loc, 'consents'))}</h2>"
    out += table([ct(loc, "kind"), ct(loc, "name"), ct(loc, "by"), ct(loc, "granted")], cons) if cons else \
        f"<p>{e(ct(loc, 'nothing'))}</p>"
    docs = [("", "–")] + [(d.document_id, d.title) for d in care.documents if d.resident_id == r.resident_id]
    reps = [(x, ct(loc, "rep." + x) if x != "none" else ct(loc, "none")) for x in ("none", "guardian", "power_of_attorney", "lpa_health")]
    out += f"<h2>{e(ct(loc, 'add_contact'))}</h2>" + form("/care/contact", _back(r.resident_id, "contacts") +
        text_input("name", ct(loc, "name"), required=True) + text_input("relation", ct(loc, "relation"), required=True) +
        text_input("phone", ct(loc, "phone"), required=True) + text_input("mail", ct(loc, "mail")) +
        select("emergency", ct(loc, "emergency"), [("0", ct(loc, "no")), ("1", ct(loc, "yes"))]) +
        select("representation", ct(loc, "representative"), reps) + select("consent_document_id", ct(loc, "consent_doc"), docs) +
        button(ct(loc, "save")))
    return out


def _tab_diagnoses(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    dx = [[e(d.icd10), e(d.text), e(ct(loc, "dx." + d.kind)), _day(loc, d.diagnosed_on), e(d.entered_by)]
          for d in care.diagnoses if d.resident_id == r.resident_id]
    al = [[e(a.substance), e(a.reaction), e(a.severity), e(a.source)] for a in care.allergies if a.resident_id == r.resident_id]
    out = f'<p class="hint">{e(ct(loc, "dx_readonly"))}</p>'
    out += table([ct(loc, "icd"), ct(loc, "diagnosis"), ct(loc, "kind"), ct(loc, "since_date"), ct(loc, "doctor")], dx)
    out += f"<h2>{e(ct(loc, 'allergies'))}</h2>"
    out += table([ct(loc, "substance"), ct(loc, "reaction"), ct(loc, "severity"), ct(loc, "source")], al) if al else \
        f"<p>{e(ct(loc, 'no_allergies'))}</p>"
    docs = [(d.document_id, d.title) for d in care.documents if d.resident_id == r.resident_id]
    out += f"<h2>{e(ct(loc, 'add_allergy'))}</h2>" + (form("/care/allergy", _back(r.resident_id, "diagnoses") +
        text_input("substance", ct(loc, "substance"), required=True) + text_input("reaction", ct(loc, "reaction"), required=True) +
        select("severity", ct(loc, "severity"), [(x, ct(loc, "sev." + x)) for x in ("mild", "moderate", "severe")]) +
        select("source_document_id", ct(loc, "source_doc"), docs, required=True) + button(ct(loc, "save")))
        if docs else f'<p class="hint">{e(ct(loc, "file_doc"))}: {e(ct(loc, "tab.documents"))}</p>')
    return out


def _value(o) -> str:
    v = f"{o.value:g}/{o.value2:g}" if o.value2 is not None else f"{o.value:g}"
    return f"{v} {o.unit}"


def _trend(loc: str, points: list[tuple[str, float]], label: str) -> str:
    if len(points) < 2:
        return ""
    values = [v for _, v in points]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    w, h, n = 520, 110, len(points)
    coords = " ".join(f"{10 + i * (w - 20) / (n - 1):.1f},{h - 10 - (v - lo) / span * (h - 30):.1f}"
                      for i, (_, v) in enumerate(points))
    return (f'<figure class="trend"><svg role="img" aria-label="{e(label)}: {e(points[0][0])} – {e(points[-1][0])}, '
            f'{lo:g}–{hi:g}" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><polyline fill="none" stroke="currentColor" '
            f'stroke-width="2" points="{coords}"/><text x="10" y="12" font-size="11">{hi:g}</text>'
            f'<text x="10" y="{h - 2}" font-size="11">{lo:g}</text></svg><figcaption>{e(label)}</figcaption></figure>')


def _tab_vitals(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    kind = q.get("kind", "") if q.get("kind", "") in OBS_KINDS else ""
    obs = sorted((o for o in care.observations if o.resident_id == r.resident_id and (not kind or o.kind == kind)),
                 key=lambda o: (o.measured_at, o.obs_id), reverse=True)
    std = care.standard.vital_ranges
    rows = []
    for o in obs:
        rng = std.get(o.kind)
        flag = " " + badge(ct(loc, "outside"), "warn") if rng and rng.outside(o.value) else ""
        rows.append([_stamp(loc, o.measured_at), e(ct(loc, "obs." + o.kind)), e(_value(o)) + flag, e(o.measured_by),
                     e(o.source_ref or ""), e(o.note)])
    shown, page_no, pages = paginate(rows, page_number(q))
    kinds = [("", ct(loc, "all"))] + [(k, ct(loc, "obs." + k)) for k in OBS_KINDS]
    out = form(f"/care/residents/{r.resident_id}", hidden(tab="vitals") + select("kind", ct(loc, "kind"), kinds, kind)
               + button(ct(loc, "show"), kind="secondary"), method="get", cls="form inline")
    weights = [o for o in obs if o.kind == "weight"]
    heights = [o for o in care.observations if o.resident_id == r.resident_id and o.kind == "height"]
    if weights and heights:
        bmi = weights[0].value / (heights[-1].value / 100) ** 2
        out += f'<p>{e(ct(loc, "bmi"))}: <strong>{bmi:.1f}</strong></p>'
    if kind:
        series = [(fmt_short(loc, datetime.strptime(o.measured_at, "%Y-%m-%d %H:%M").date()), o.value) for o in reversed(obs)]
        out += _trend(loc, series[-30:], f'{ct(loc, "trend")} {ct(loc, "obs." + kind)}')
        rng = std.get(kind)
        if rng:
            parts = [f'{ct(loc, "low")} {rng.low:g}' if rng.low is not None else "",
                     f'{ct(loc, "high")} {rng.high:g}' if rng.high is not None else ""]
            out += f'<p class="hint">{e(ct(loc, "house_range"))}: {e(" / ".join(p for p in parts if p))}</p>'
    out += table([ct(loc, "measured_at"), ct(loc, "kind"), ct(loc, "value"), ct(loc, "measured_by"), ct(loc, "source"),
                  ct(loc, "note")], shown) if shown else f"<p>{e(ct(loc, 'nothing'))}</p>"
    out += pager(loc, f"/care/residents/{r.resident_id}", page_no, pages, len(rows), tab="vitals", kind=kind)
    units = sorted({u for k in OBS_KINDS for u in (UNITS.get(k) or (("mg/dl",) if care.edition == "de" else ("mmol/L",)))})
    now = ws.app.now(ws.state.current_date)
    out += f"<h2>{e(ct(loc, 'record_value'))}</h2>" + form("/care/observation", _back(r.resident_id, "vitals") +
        select("kind", ct(loc, "kind"), [(k, ct(loc, "obs." + k)) for k in OBS_KINDS], kind or "bp", required=True) +
        text_input("value", ct(loc, "value"), "", kind="number", required=True, extra='step="any"') +
        text_input("value2", ct(loc, "value2"), "", kind="number", extra='step="any"') +
        select("unit", ct(loc, "unit_of"), [(u, u) for u in units], "mmHg", required=True) +
        text_input("measured_at", ct(loc, "measured_at") + " (YYYY-MM-DD HH:MM)", now, required=True) +
        text_input("measured_by", ct(loc, "measured_by")) + text_input("source_ref", ct(loc, "source")) +
        text_input("note", ct(loc, "note")) + button(ct(loc, "save")))
    return out


def _tab_fluids(ws, r, q) -> str:
    loc, care, today = _loc(ws), ws.care, ws.state.current_date.isoformat()
    rows = sorted((f for f in care.fluids if f.resident_id == r.resident_id), key=lambda f: (f.at, f.entry_id), reverse=True)
    intake = sum(f.ml for f in rows if f.direction == "in" and f.at.startswith(today))
    out = f'<p>{e(ct(loc, "total_today"))}: <strong>{intake} ml</strong> · {e(ct(loc, "std.fluids"))}: ' \
          f'{care.standard.fluid_target_ml} ml</p>'
    shown, page_no, pages = paginate(rows, page_number(q))
    out += table([ct(loc, "at"), ct(loc, "direction"), ct(loc, "kind"), ct(loc, "ml"), ct(loc, "source"), ct(loc, "by")],
                 [[_stamp(loc, f.at), e(ct(loc, "dir." + f.direction)), e(ct(loc, "fl." + f.kind)), str(f.ml),
                   e(f.source_ref or ""), e(_initials(f.entered_by))] for f in shown]) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    out += pager(loc, f"/care/residents/{r.resident_id}", page_no, pages, len(rows), tab="fluids")
    kinds = [(k, ct(loc, "fl." + k)) for k in ("drink", "tube_feed", "infusion", "urine", "vomit", "drain")]
    out += f"<h2>{e(ct(loc, 'record_value'))}</h2>" + form("/care/fluid", _back(r.resident_id, "fluids") +
        select("direction", ct(loc, "direction"), [("in", ct(loc, "dir.in")), ("out", ct(loc, "dir.out"))], "in") +
        select("kind", ct(loc, "kind"), kinds, "drink") + text_input("ml", ct(loc, "ml"), "", kind="number", required=True) +
        text_input("at", ct(loc, "at") + " (YYYY-MM-DD HH:MM)", ws.app.now(ws.state.current_date), required=True) +
        text_input("source_ref", ct(loc, "source")) + button(ct(loc, "save")))
    return out


def _tab_excretion(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    rows = sorted((x for x in care.excretions if x.resident_id == r.resident_id), key=lambda x: (x.at, x.entry_id), reverse=True)
    shown, page_no, pages = paginate(rows, page_number(q))
    out = table([ct(loc, "at"), ct(loc, "kind"), ct(loc, "continence"), ct(loc, "note")],
                [[_stamp(loc, x.at), e(ct(loc, "ex." + x.kind)), e(ct(loc, "co." + x.continence)), e(x.note)]
                 for x in shown]) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    out += pager(loc, f"/care/residents/{r.resident_id}", page_no, pages, len(rows), tab="excretion")
    out += f"<h2>{e(ct(loc, 'record_value'))}</h2>" + form("/care/excretion", _back(r.resident_id, "excretion") +
        select("kind", ct(loc, "kind"), [("urine", ct(loc, "ex.urine")), ("stool", ct(loc, "ex.stool"))], "urine") +
        select("continence", ct(loc, "continence"), [(c, ct(loc, "co." + c)) for c in ("continent", "incontinent", "catheter")]) +
        text_input("at", ct(loc, "at") + " (YYYY-MM-DD HH:MM)", ws.app.now(ws.state.current_date), required=True) +
        text_input("note", ct(loc, "note")) + button(ct(loc, "save")))
    return out


def _tab_assessments(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    rows = sorted((a for a in care.assessments if a.resident_id == r.resident_id),
                  key=lambda a: (a.performed_at, a.assessment_id), reverse=True)
    out = table([ct(loc, "performed"), ct(loc, "scale"), ct(loc, "total"), ct(loc, "risk_class"), ct(loc, "next_due"),
                 ct(loc, "by")],
                [[_stamp(loc, a.performed_at), e(scales.get_scale(a.scale_id).title[loc]), str(a.total),
                  e(ct(loc, "rc." + a.risk_class)), _day(loc, a.next_due), e(a.performed_by)] for a in rows]) \
        if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    links = " ".join(f'<a class="btn secondary" href="{e(url("/care/residents/" + r.resident_id + "/assess", scale=s.scale_id))}">'
                     f'{e(s.title[loc])}</a>' for s in scales.SCALES.values())
    return out + f"<h2>{e(ct(loc, 'new_assessment'))}</h2><p>{links}</p>"


def assessment_form(ws, resident_id: str, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc = _loc(ws)
    r = ws.care.resident(resident_id)
    try:
        scale = scales.get_scale(q.get("scale", ""))
    except scales.ScaleError:
        scale = None
    if r is None or scale is None:
        return ct(loc, "new_assessment"), "care", f"<p>{e(ct(loc, 'nothing'))}</p>", _side(ws, "residents")
    fields = ""
    for item in scale.items:
        opts = "".join(f'<span class="check"><input type="radio" id="i-{e(item.key)}-{e(o.key)}" name="a.{e(item.key)}" '
                       f'value="{e(o.key)}" required><label for="i-{e(item.key)}-{e(o.key)}">{e(o.label[loc])}</label></span>'
                       for o in item.options)
        fields += f'<fieldset><legend>{e(item.label[loc])}</legend>{opts}</fieldset>'
    body = form("/care/assessment", _back(resident_id, "assessments") + hidden(scale_id=scale.scale_id) + fields +
                text_input("performed_at", ct(loc, "performed") + " (YYYY-MM-DD HH:MM)", ws.app.now(ws.state.current_date),
                           required=True) + text_input("source_refs", ct(loc, "source")) + text_input("note", ct(loc, "note")) +
                button(ct(loc, "submit")))
    return f"{scale.title[loc]} – {r.name}", "care", body, _side(ws, "residents")


def _tab_risks(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    rows = [[e(ct(loc, "rk." + x.kind)), e(ct(loc, "yes" if x.present else "no")), e(x.reason), _day(loc, x.since),
             _day(loc, x.review_on)] for x in sorted((x for x in care.risks if x.resident_id == r.resident_id),
                                                     key=lambda x: x.kind)]
    out = table([ct(loc, "risk"), ct(loc, "present"), ct(loc, "reason"), ct(loc, "since"), ct(loc, "plan_review_on")], rows) \
        if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    kinds = [(k, ct(loc, "rk." + k)) for k in ("pressure_ulcer", "falls", "dehydration", "malnutrition", "contractures",
                                               "pain", "incontinence")]
    out += f"<h2>{e(ct(loc, 'set_risk'))}</h2>" + form("/care/risk", _back(r.resident_id, "risks") +
        select("kind", ct(loc, "risk"), kinds) + select("present", ct(loc, "present"), [("1", ct(loc, "yes")), ("0", ct(loc, "no"))]) +
        text_input("reason", ct(loc, "reason"), required=True) + text_input("review_on", ct(loc, "plan_review_on"), kind="date") +
        button(ct(loc, "save")))
    return out


def _schedule_text(loc: str, s) -> str:
    if s.kind == "interval":
        night = f" / {ct(loc, 'night')} {s.night_every_hours} h" if s.night_every_hours else ""
        return f"{s.every_hours} h{night} ({s.anchor})"
    days = ""
    if s.kind == "weekly":
        names = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So") if loc == "de-DE" else ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
        days = ", ".join(names[d] for d in s.weekdays) + ": "
    return days + ", ".join(s.times)


def _review(ws, entry) -> str:
    """The review date with a small form to move it (an entry past its date needs a new one)."""
    loc = _loc(ws)
    return _day(loc, entry.review_on) + form("/care/careplan/update", hidden(back=f"/care/residents/{entry.resident_id}?tab=careplan",
                                                                           entry_id=entry.entry_id) +
                                             text_input("review_on", ct(loc, "plan_review_on"), kind="date", ident=f"rv-{entry.entry_id}") +
                                             button(ct(loc, "save"), kind="secondary"), cls="form inline")


def _tab_careplan(ws, r, q) -> str:
    loc, care, rid = _loc(ws), ws.care, r.resident_id
    model = care.careplan_model
    entries = [x for x in care.careplan if x.resident_id == rid]
    if model == "sis":
        head = [careplan.FIELD_LABELS["theme"][loc], careplan.FIELD_LABELS["finding"][loc], ct(loc, "plan_review_on"), ct(loc, "status")]
        rows = [[e(careplan.SIS_THEMES.get(x.theme, {}).get(loc, x.theme)), e(x.finding), _review(ws, x), e(ct(loc, "cps." + x.status))]
                for x in entries]
    else:
        head = [careplan.FIELD_LABELS[k][loc] for k in ("problem", "resource", "goal")] + [ct(loc, "plan_review_on"), ct(loc, "status")]
        rows = [[e(x.problem), e(x.resource), e(x.goal), _review(ws, x), e(ct(loc, "cps." + x.status))]
                for x in entries]
    out = f"<h2>{e(ct(loc, 'entries'))}</h2>" + (table(head, rows) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>")
    measures = [m for m in care.measures if m.resident_id == rid]
    mrows = [[e(m.label), e(_schedule_text(loc, m.schedule)), str(m.duration_min), e(ct(loc, "q." + m.qualification.value)) +
              (" + " + e(", ".join(ct(loc, "skill." + s) for s in m.skills)) if m.skills else ""),
              _valid(loc, m.start, m.end), e(ct(loc, "from_order") if m.order_id else ""),
              (form("/care/measure/end", _back(rid, "careplan") + hidden(measure_id=m.measure_id, end=ws.state.current_date.isoformat())
                    + text_input("reason", ct(loc, "reason"), ident=f"r-{m.measure_id}") + button(ct(loc, "end_measure"), kind="secondary"),
                    cls="form inline") if m.end is None and not (m.order_id and m.catalogue_key == "insulin") else "")]
             for m in measures]
    out += f"<h2>{e(ct(loc, 'measures'))}</h2>" + (table([ct(loc, "measure"), ct(loc, "schedule"), ct(loc, "duration"),
                                                          ct(loc, "qualification"), ct(loc, "valid"), ct(loc, "source"), ""], mrows)
                                                    if mrows else f"<p>{e(ct(loc, 'nothing'))}</p>")
    fields = (select("theme", careplan.FIELD_LABELS["theme"][loc], [(k, v[loc]) for k, v in careplan.SIS_THEMES.items()]) +
              textarea("finding", careplan.FIELD_LABELS["finding"][loc], required=True)) if model == "sis" else \
        (textarea("problem", careplan.FIELD_LABELS["problem"][loc], required=True) +
         textarea("resource", careplan.FIELD_LABELS["resource"][loc]) + textarea("goal", careplan.FIELD_LABELS["goal"][loc], required=True))
    out += f"<h2>{e(ct(loc, 'add_entry'))}</h2>" + form("/care/careplan", _back(rid, "careplan") + fields +
                                                        text_input("review_on", ct(loc, "plan_review_on"), kind="date", required=True) +
                                                        button(ct(loc, "save")))
    cat = [(c.key, c.label[loc]) for c in care.standard.catalogue if c.key != "round_escort"]
    orders = [("", "–")] + [(o.order_id, o.text[:60]) for o in care.orders if o.resident_id == rid]
    ents = [("", "–")] + [(x.entry_id, (x.finding or x.problem)[:60]) for x in entries]
    out += f"<h2>{e(ct(loc, 'add_measure'))}</h2>" + form("/care/measure", _back(rid, "careplan") +
        select("catalogue_key", ct(loc, "measure"), cat, required=True) +
        select("schedule_kind", ct(loc, "schedule"), [(k, ct(loc, "sk." + k)) for k in ("times", "interval", "weekly")]) +
        text_input("times", ct(loc, "times")) + text_input("every_hours", ct(loc, "every_hours"), kind="number") +
        text_input("night_every_hours", ct(loc, "night_every_hours"), kind="number") +
        text_input("weekdays", ct(loc, "weekdays")) +
        text_input("start", ct(loc, "start"), ws.state.current_date.isoformat(), kind="date", required=True) +
        text_input("end", ct(loc, "end"), kind="date") + select("entry_id", ct(loc, "entry"), ents) +
        select("order_id", ct(loc, "order"), orders) + button(ct(loc, "save")))
    return out


def _tab_report(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    rows = sorted((x for x in care.reports if x.resident_id == r.resident_id), key=lambda x: (x.at, x.entry_id), reverse=True)
    table_rows = [[_stamp(loc, x.at), e(ct(loc, "cat." + x.category)), e(x.text) +
                   (f' <span class="hint">{e(ct(loc, "correction_of"))} {e(x.correction_of)}</span>' if x.correction_of else ""),
                   e(", ".join(x.informed)), e(x.author)] for x in rows]
    shown, page_no, pages = paginate(table_rows, page_number(q))
    today = ws.state.current_date.isoformat()
    recent = [o for o in care.observations if o.resident_id == r.resident_id and o.measured_at[:10] == today]
    links = "".join(f'<span class="check"><input type="checkbox" id="lo-{e(o.obs_id)}" name="observation_ids" value="{e(o.obs_id)}">'
                    f'<label for="lo-{e(o.obs_id)}">{e(ct(loc, "obs." + o.kind))} {e(_value(o))} ({e(o.measured_at[11:])})</label></span>'
                    for o in recent)
    out = form("/care/report", _back(r.resident_id, "report") +
               select("category", ct(loc, "category"), [(c, ct(loc, "cat." + c)) for c in
                                                        ("general", "observation", "incident", "communication", "handover")]) +
               textarea("text", ct(loc, "text"), required=True, rows=3) + text_input("informed", ct(loc, "informed")) +
               (f'<fieldset><legend>{e(ct(loc, "linked_values"))}</legend>{links}</fieldset>' if links else "") +
               button(ct(loc, "write_entry")))
    out += table([ct(loc, "at"), ct(loc, "category"), ct(loc, "text"), ct(loc, "informed"), ct(loc, "by")], shown) \
        if shown else f"<p>{e(ct(loc, 'nothing'))}</p>"
    return out + pager(loc, f"/care/residents/{r.resident_id}", page_no, pages, len(table_rows), tab="report")


def _order_rows(ws, orders, back: str, with_resident: bool) -> list[list[str]]:
    loc, care = _loc(ws), ws.care
    rows = []
    for o in orders:
        doc = care.physician(o.physician_id)
        ack = (e(o.acknowledged_by) + " · " + _stamp(loc, o.acknowledged_at)) if o.acknowledged_by else \
            form("/care/acknowledge", hidden(order_id=o.order_id, back=back) + button(ct(loc, "acknowledge")), cls="form inline")
        row = [_stamp(loc, o.entered_at), e(doc.name if doc else ""), e(o.text),
               _valid(loc, o.valid_from, o.valid_to), ack]
        if with_resident:
            res = care.resident(o.resident_id)
            row.insert(1, f'<a href="/care/residents/{e(o.resident_id)}?tab=orders">{e(res.name if res else "")}</a>')
        rows.append(row)
    return rows


def _tab_orders(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    orders = sorted((o for o in care.orders if o.resident_id == r.resident_id), key=lambda o: (o.entered_at, o.order_id), reverse=True)
    rows = _order_rows(ws, orders, f"/care/residents/{r.resident_id}?tab=orders", False)
    return f'<p class="hint">{e(ct(loc, "orders_readonly"))}</p>' + (table(
        [ct(loc, "at"), ct(loc, "doctor"), ct(loc, "order"), ct(loc, "valid"), ct(loc, "acknowledged")], rows)
        if rows else f"<p>{e(ct(loc, 'nothing'))}</p>")


def _tab_medication(ws, r, q) -> str:
    loc, care = _loc(ws), ws.care
    lines = [m for m in care.medication if m.resident_id == r.resident_id]
    rows = [[e(m.drug), e(m.strength), e(m.form), e(m.morning), e(m.noon), e(m.evening), e(m.night),
             e(f"{ct(loc, 'prn')} ({ct(loc, 'max_day')} {m.prn_max_per_day})" if m.prn else ""), e(m.indication),
             _day(loc, m.start)] for m in lines]
    return f'<p class="hint">{e(ct(loc, "med_readonly"))}</p>' + table(
        [ct(loc, "drug"), ct(loc, "strength"), ct(loc, "form"), ct(loc, "mo"), ct(loc, "noon"), ct(loc, "ev"),
         ct(loc, "night"), ct(loc, "prn"), ct(loc, "indication"), ct(loc, "start")], rows)


def _tab_appointments(ws, r, q) -> str:
    loc, care, rid = _loc(ws), ws.care, r.resident_id
    rows = []
    for a in sorted((a for a in care.appointments if a.resident_id == rid), key=lambda a: (a.date, a.start)):
        cancel = form("/care/appointment/cancel", _back(rid, "appointments") + hidden(appointment_id=a.appointment_id) +
                      text_input("reason", ct(loc, "reason"), ident=f"c-{a.appointment_id}") + button(ct(loc, "cancel"), kind="secondary"),
                      cls="form inline") if a.status == "planned" else ""
        rows.append([_day(loc, a.date), e(f"{a.start}–{a.end}"), e(ct(loc, "ap." + a.kind)), e(a.place), e(a.with_whom),
                     e(ct(loc, "yes" if a.transport_needed else "no")),
                     e(ct(loc, "q." + a.escort_qualification.value) if a.escort_qualification else "–"),
                     e(ct(loc, "aps." + a.status)), cancel])
    out = table([ct(loc, "date"), ct(loc, "time"), ct(loc, "kind"), ct(loc, "place"), ct(loc, "with"), ct(loc, "transport"),
                 ct(loc, "escort"), ct(loc, "status"), ""], rows) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    out += f"<h2>{e(ct(loc, 'new_appointment'))}</h2>" + form("/care/appointment", _back(rid, "appointments") +
        select("kind", ct(loc, "kind"), [(k, ct(loc, "ap." + k)) for k in ("gp_visit", "specialist", "therapy", "hospital",
                                                                         "relatives", "other")]) +
        text_input("date", ct(loc, "date"), ws.state.current_date.isoformat(), kind="date", required=True) +
        text_input("start", ct(loc, "start") + " (HH:MM)", required=True) + text_input("end", ct(loc, "end") + " (HH:MM)", required=True) +
        text_input("place", ct(loc, "place"), required=True) + text_input("with_whom", ct(loc, "with")) +
        select("transport_needed", ct(loc, "transport"), [("0", ct(loc, "no")), ("1", ct(loc, "yes"))]) +
        select("escort_qualification", ct(loc, "escort"), [("", "–"), ("registered_nurse", ct(loc, "q.registered_nurse")),
                                                            ("care_assistant", ct(loc, "q.care_assistant"))]) +
        button(ct(loc, "save")))
    return out


def _tab_wounds(ws, r, q) -> str:
    loc, care, rid = _loc(ws), ws.care, r.resident_id
    out = ""
    for w in (w for w in care.wounds if w.resident_id == rid):
        head = (f'{e(ct(loc, "wk." + w.kind))}{" " + e(w.category) if w.category else ""} · {e(w.location)} · '
                f'{e(ct(loc, "first_seen"))} {_day(loc, w.first_seen)} · {e(ct(loc, "or." + w.origin))} · {e(ct(loc, "ws." + w.status))}')
        rows = [[_stamp(loc, a.at), f"{a.length_mm}×{a.width_mm}×{a.depth_mm}", e(ct(loc, "ex2." + a.exudate)), e(a.wound_bed),
                 e(a.dressing), _day(loc, a.next_change), e(ct(loc, "yes" if a.photo_taken else "no")), e(a.by)]
                for a in care.wound_assessments if a.wound_id == w.wound_id]
        out += f"<h2>{head}</h2>" + (table([ct(loc, "at"), ct(loc, "size"), ct(loc, "exudate"), ct(loc, "wound_bed"),
                                             ct(loc, "dressing"), ct(loc, "next_change"), ct(loc, "photo"), ct(loc, "by")], rows)
                                      if rows else "")
        if w.status == "open":
            out += form("/care/wound/assess", _back(rid, "wounds") + hidden(wound_id=w.wound_id) +
                        text_input("at", ct(loc, "at") + " (YYYY-MM-DD HH:MM)", ws.app.now(ws.state.current_date), required=True, ident=f"at-{w.wound_id}") +
                        text_input("length_mm", ct(loc, "length"), kind="number", required=True, ident=f"l-{w.wound_id}") +
                        text_input("width_mm", ct(loc, "width"), kind="number", required=True, ident=f"w-{w.wound_id}") +
                        text_input("depth_mm", ct(loc, "depth"), kind="number", required=True, ident=f"d-{w.wound_id}") +
                        select("exudate", ct(loc, "exudate"), [(x, ct(loc, "ex2." + x)) for x in ("none", "low", "moderate", "high")], ident=f"x-{w.wound_id}") +
                        text_input("wound_bed", ct(loc, "wound_bed"), ident=f"b-{w.wound_id}") +
                        text_input("dressing", ct(loc, "dressing"), ident=f"v-{w.wound_id}") +
                        text_input("next_change", ct(loc, "next_change"), kind="date", ident=f"n-{w.wound_id}") +
                        select("photo_taken", ct(loc, "photo"), [("0", ct(loc, "no")), ("1", ct(loc, "yes"))], ident=f"p-{w.wound_id}") +
                        select("healed", ct(loc, "healed"), [("0", ct(loc, "no")), ("1", ct(loc, "yes"))], ident=f"h-{w.wound_id}") +
                        button(ct(loc, "assess_wound")))
    if not out:
        out = f"<p>{e(ct(loc, 'nothing'))}</p>"
    out += f"<h2>{e(ct(loc, 'new_wound'))}</h2>" + form("/care/wound", _back(rid, "wounds") +
        text_input("location", ct(loc, "location"), required=True) +
        select("kind", ct(loc, "wound_kind"), [(k, ct(loc, "wk." + k)) for k in ("pressure_ulcer", "skin_tear", "leg_ulcer", "surgical", "other")]) +
        select("category", ct(loc, "category_w"), [("", "–"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("unstageable", "?")]) +
        text_input("first_seen", ct(loc, "first_seen"), ws.state.current_date.isoformat(), kind="date", required=True) +
        select("origin", ct(loc, "origin"), [("in_house", ct(loc, "or.in_house")), ("brought_in", ct(loc, "or.brought_in"))]) +
        button(ct(loc, "save")))
    return out


def _tab_documents(ws, r, q) -> str:
    loc, care, rid = _loc(ws), ws.care, r.resident_id
    rows = [[_day(loc, d.dated), e(ct(loc, "doc_kind." + d.kind)), e(d.title), e(d.sender),
             f'<a href="/care/documents/{e(d.document_id)}">{e(ct(loc, "open_pdf"))}</a>']
            for d in sorted((d for d in care.documents if d.resident_id == rid), key=lambda d: (d.filed_at, d.document_id), reverse=True)]
    out = table([ct(loc, "dated"), ct(loc, "kind"), ct(loc, "title"), ct(loc, "sender"), ""], rows) if rows else \
        f"<p>{e(ct(loc, 'nothing'))}</p>"
    atts = [(a.attachment_id, f"{a.filename} – {m.subject}") for m in ws.app.mails for a in m.attachments
            if a.media_type == "application/pdf"]
    if atts:
        out += f"<h2>{e(ct(loc, 'file_doc'))}</h2>" + form("/care/document", _back(rid, "documents") +
            select("attachment_id", ct(loc, "attachment"), atts, required=True) +
            select("kind", ct(loc, "kind"), [(k, ct(loc, "doc_kind." + k)) for k in ("discharge_letter", "findings", "lab_report",
                                                                                   "consent_form", "transport_form", "other")]) +
            text_input("title", ct(loc, "title"), required=True) + button(ct(loc, "file")))
    return out


AIDS = ("glasses", "hearing_aid", "dentures", "walking_frame", "wheelchair")


def _tab_transfer(ws, r, q) -> str:
    loc, care, rid = _loc(ws), ws.care, r.resident_id
    sheets = [x for x in care.transfer_sheets if x.resident_id == rid]
    rows = [[_stamp(loc, x.saved_at), e(x.destination), e(x.reason), e(ct(loc, "mob." + x.mobility)),
             e(", ".join(ct(loc, "aid." + a) for a in x.aids_sent)), e(x.saved_by)] for x in reversed(sheets)]
    out = (f"<h2>{e(ct(loc, 'saved_sheets'))}</h2>" + table([ct(loc, "at"), ct(loc, "destination"), ct(loc, "reason"),
                                                             ct(loc, "mobility"), ct(loc, "aids"), ct(loc, "by")], rows)) if rows else ""
    aids = "".join(f'<span class="check"><input type="checkbox" id="aid-{a}" name="aids_sent" value="{a}">'
                   f'<label for="aid-{a}">{e(ct(loc, "aid." + a))}</label></span>' for a in AIDS)
    hospital = next((c.organisation for c in ws.app.contacts if c.contact_id == "care-hospital"), "")
    out += f"<h2>{e(ct(loc, 'save_sheet'))}</h2>" + form("/care/transfer", _back(rid, "transfer") +
        text_input("destination", ct(loc, "destination"), hospital, required=True) + textarea("reason", ct(loc, "reason"), required=True) +
        select("mobility", ct(loc, "mobility"), [(x, ct(loc, "mob." + x)) for x in ("walks_alone", "walks_with_aid", "wheelchair", "bedbound")]) +
        select("continence", ct(loc, "continence"), [(x, ct(loc, "con." + x)) for x in ("continent", "urine_incontinent", "double_incontinent", "catheter")]) +
        select("orientation", ct(loc, "orientation"), [(x, ct(loc, "ori." + x)) for x in ("oriented", "partly_oriented", "disoriented")]) +
        f'<fieldset><legend>{e(ct(loc, "aids"))}</legend>{aids}</fieldset>' +
        select("isolation", ct(loc, "isolation"), [("0", ct(loc, "no")), ("1", ct(loc, "yes"))]) +
        text_input("contact_for_questions", ct(loc, "contact_q")) + button(ct(loc, "save_sheet")))
    if sheets:
        out += f"<h2>{e(ct(loc, 'print'))}</h2>"  # what goes with the resident: the house standard's transfer printouts
        for kind in dict.fromkeys(["transfer_sheet", "medication_plan", *ws.care.standard.transfer_prints]):
            out += _print_form(ws, rid, kind, f"/care/residents/{rid}?tab=transfer")
    return out


def _print_form(ws, rid: str | None, kind: str, back: str, ref: str = "") -> str:
    loc = _loc(ws)
    ident = f"{kind}-{rid or ref}".replace("/", "-")
    return form("/print/submit", hidden(back=back, kind=kind, resident_id=rid, ref=ref) +
                select("printer", ct(loc, "printer"), [(p, p) for p in ws.care.printers], ident=f"pr-{ident}") +
                text_input("copies", ct(loc, "copies"), "1", kind="number", ident=f"cp-{ident}") +
                button(f'{ct(loc, "print")}: {ct(loc, "print_kind." + kind)}', kind="secondary"), cls="form inline")


def document_pdf(ws, document_id: str) -> tuple[bytes, str] | None:
    from opsdesk.workforce import pdf
    d = next((x for x in ws.care.documents if x.document_id == document_id), None)
    if d is None:
        return None
    return pdf.render(d.text, d.title), d.title


def admit_form(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care = _loc(ws), ws.care
    taken = {r.room for r in care.residents if r.status != "moved_out"}
    gps = [(p.physician_id, f"{p.name}, {p.practice}") for p in care.physicians if p.kind == "gp"]
    level = (select("care_level", ct(loc, "care_level"), [(str(i), str(i)) for i in range(1, 6)], "3") if care.edition == "de"
             else text_input("funding_note", ct(loc, "funding")))
    body = form("/care/admit", hidden(back="/care/residents") +
                text_input("given_name", ct(loc, "given_name"), required=True) + text_input("family_name", ct(loc, "family_name"), required=True) +
                select("sex", ct(loc, "sex"), [("f", ct(loc, "sex.f")), ("m", ct(loc, "sex.m"))]) +
                text_input("birth_date", ct(loc, "birth_date"), kind="date", required=True) +
                text_input("birth_place", ct(loc, "birth_place"), required=True) +
                select("ward_id", ct(loc, "unit"), [(w.ward_id, _unit_name(ws, w.ward_id)) for w in ws.state.wards]) +
                text_input("room", ct(loc, "room"), required=True) + select("gp_id", ct(loc, "gp"), gps) + level +
                text_input("religion", ct(loc, "religion")) +
                select("advance_directive", ct(loc, "advance_directive"), [("0", ct(loc, "no")), ("1", ct(loc, "yes"))]) +
                button(ct(loc, "admit")))
    hint = f'<p class="hint">{e(ct(loc, "room"))}: {e(", ".join(sorted(taken)))} – {e(ct(loc, "status.present"))}</p>'
    return ct(loc, "nav.admit"), "care", hint + body, _side(ws, "admit")


def print_queue(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care = _loc(ws), ws.care
    rows = []
    for j in reversed(care.print_jobs):
        res = care.resident(j.resident_id) if j.resident_id else None
        cancel = form("/print/cancel", hidden(job_id=j.job_id, back="/print") + button(ct(loc, "cancel"), kind="secondary"),
                      cls="form inline") if j.status == "queued" else ""
        rows.append([_stamp(loc, j.submitted_at), e(ct(loc, "print_kind." + j.kind)), e(res.name if res else j.ref),
                     e(j.printer), str(j.copies), e(ct(loc, "pst." + j.status)), cancel])
    body = table([ct(loc, "at"), ct(loc, "what"), ct(loc, "resident"), ct(loc, "printer"), ct(loc, "copies"), ct(loc, "status"), ""],
                 rows) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    return ct(loc, "queue"), "care", body, _side(ws, "print")


def rounds(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care, st = _loc(ws), ws.care, ws.state
    rows = []
    for rp in care.rounds:
        doc = care.physician(rp.physician_id)
        pts = "; ".join(f"{care.resident(rid).name}: {', '.join(p)}" for rid, p in rp.points.items() if care.resident(rid))
        rows.append([_day(loc, rp.date), e(rp.start), e(doc.name if doc else ""), e(_staff_name(ws, rp.escort_id)), e(pts),
                     _print_form(ws, None, "round_sheet", "/care/rounds", ref=rp.round_id)])
    body = (f"<h2>{e(ct(loc, 'rounds_planned'))}</h2>" + table([ct(loc, "date"), ct(loc, "time"), ct(loc, "doctor"),
                                                                ct(loc, "escort_nurse"), ct(loc, "points"), ""], rows)) if rows else ""
    doc_id = q.get("doctor") or (care.physicians[0].physician_id if care.physicians else "")
    pick = form("/care/rounds", select("doctor", ct(loc, "doctor"), [(p.physician_id, f"{p.name}, {p.practice}") for p in care.physicians], doc_id)
                + button(ct(loc, "show"), kind="secondary"), method="get", cls="form inline")
    doc = care.physician(doc_id)
    patients = [r for r in care.residents if r.status == "present" and (doc is None or doc.kind != "gp" or r.gp_id == doc.physician_id)]
    codes = ["order_to_acknowledge", "new_order", "wound", "other"] + [f"assessment_due:{s}" for s in scales.SCALES] + \
        [f"value_outside:{k}" for k in ("bp", "pulse", "temperature", "spo2", "glucose", "weight")]
    def label(code: str) -> str:
        head, _, tail = code.partition(":")
        extra = (scales.SCALES[tail].title[loc] if tail in scales.SCALES else ct(loc, "obs." + tail)) if tail else ""
        return ct(loc, "pt." + head) + (f" – {extra}" if extra else "")
    lines = ""
    for r in patients:
        boxes = "".join(f'<span class="check"><input type="checkbox" id="p-{r.resident_id}-{c}" name="p.{r.resident_id}" value="{c}">'
                        f'<label for="p-{r.resident_id}-{c}">{e(label(c))}</label></span>' for c in codes)
        lines += f'<fieldset><legend>{e(r.room)} {e(r.name)}</legend>{boxes}</fieldset>'
    staff = [(x.employee_id, x.name) for x in st.employees]
    body += pick + f"<h2>{e(ct(loc, 'plan_round'))}</h2>" + form("/care/rounds", hidden(back="/care/rounds", physician_id=doc_id) +
        text_input("date", ct(loc, "date"), st.current_date.isoformat(), kind="date", required=True) +
        text_input("start", ct(loc, "start") + " (HH:MM)", required=True) + select("escort_id", ct(loc, "escort_nurse"), staff) +
        lines + button(ct(loc, "save")))
    return ct(loc, "nav.rounds"), "care", body, _side(ws, "rounds")


def escalate_form(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care = _loc(ws), ws.care
    rows = [[_stamp(loc, n.at), e(n.subject), e(n.text), e(", ".join(care.resident(x).name for x in n.resident_ids if care.resident(x)))]
            for n in reversed(care.escalations)]
    boxes = "".join(f'<span class="check"><input type="checkbox" id="er-{r.resident_id}" name="resident_ids" value="{r.resident_id}">'
                    f'<label for="er-{r.resident_id}">{e(r.room)} {e(r.name)}</label></span>' for r in care.residents if r.status != "moved_out")
    body = form("/care/escalate", hidden(back="/care/escalate") + text_input("subject", ct(loc, "subject"), required=True) +
                textarea("text", ct(loc, "text"), required=True, rows=5) +
                f'<fieldset class="picklist"><legend>{e(ct(loc, "residents_concerned"))}</legend>{boxes}</fieldset>' +
                button(ct(loc, "send_note")))
    if rows:
        body += f"<h2>{e(ct(loc, 'notes_sent'))}</h2>" + table([ct(loc, "at"), ct(loc, "subject"), ct(loc, "text"),
                                                                ct(loc, "residents_concerned")], rows)
    return ct(loc, "nav.escalate"), "care", body, _side(ws, "escalate")


def handover_form(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    """The written handover to the next shift; in the long tier also "wait until" (the clock moves, colleagues work)."""
    loc, care = _loc(ws), ws.care
    rows = [[_stamp(loc, h.written_at), e(_unit_name(ws, h.ward_id)), e(ct(loc, "shift." + h.shift)), e(h.text).replace("\n", "<br>"),
             e(", ".join(care.resident(x).name for x in h.resident_ids if care.resident(x)))] for h in reversed(care.handovers)]
    boxes = "".join(f'<span class="check"><input type="checkbox" id="hr-{r.resident_id}" name="resident_ids" value="{r.resident_id}">'
                    f'<label for="hr-{r.resident_id}">{e(r.room)} {e(r.name)}</label></span>' for r in care.residents if r.status != "moved_out")
    units = [(w.ward_id, _unit_name(ws, w.ward_id)) for w in ws.state.wards]
    shifts = [(k, ct(loc, "shift." + k)) for k in ("early", "late", "night")]
    body = form("/care/handover", hidden(back="/care/handover") + select("ward_id", ct(loc, "unit"), units) +
                select("shift", ct(loc, "shift"), shifts, "early") + textarea("text", ct(loc, "text"), required=True, rows=8) +
                f'<fieldset class="picklist"><legend>{e(ct(loc, "residents_concerned"))}</legend>{boxes}</fieldset>' +
                button(ct(loc, "save_handover")))
    if care.clock_start is not None:
        body += (f"<h2>{e(ct(loc, 'wait_title'))}</h2><p class=\"hint\">{e(ct(loc, 'wait_hint'))} "
                 f"{e(ws.app.now(ws.state.current_date)[11:])}</p>" +
                 form("/shift/wait", hidden(back="/care/handover") + text_input("until", ct(loc, "until") + " (HH:MM)", required=True) +
                      button(ct(loc, "wait"), kind="secondary"), cls="form inline"))
    if rows:
        body += f"<h2>{e(ct(loc, 'handovers'))}</h2>" + table([ct(loc, "at"), ct(loc, "unit"), ct(loc, "shift"), ct(loc, "text"),
                                                              ct(loc, "residents_concerned")], rows)
    return ct(loc, "nav.handover"), "care", body, _side(ws, "handover")


# ---------------------------------------------------------------- orders across residents, house standard

def orders(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care = _loc(ws), ws.care
    state = q.get("state", "to_acknowledge")
    chosen = [o for o in sorted(care.orders, key=lambda o: (o.entered_at, o.order_id), reverse=True)
              if state == "all" or o.acknowledged_by is None]
    rows = _order_rows(ws, chosen, url("/care/orders", state=state), True)
    shown, page_no, pages = paginate(rows, page_number(q))
    pick = form("/care/orders", select("state", ct(loc, "status"), [("to_acknowledge", ct(loc, "to_ack")), ("all", ct(loc, "all"))],
                                       state) + button(ct(loc, "show"), kind="secondary"), method="get", cls="form inline")
    body = f'<p class="hint">{e(ct(loc, "orders_readonly"))}</p>' + pick + (table(
        [ct(loc, "at"), ct(loc, "resident"), ct(loc, "doctor"), ct(loc, "order"), ct(loc, "valid"), ct(loc, "acknowledged")],
        shown) if shown else f"<p>{e(ct(loc, 'nothing'))}</p>") + pager(loc, "/care/orders", page_no, pages, len(rows), state=state)
    return ct(loc, "nav.orders"), "care", body, _side(ws, "orders")


def standard(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, s = _loc(ws), ws.care.standard
    init = [[e(scales.get_scale(a.scale_id).title[loc]), str(a.within_hours), str(s.reassess_every_days.get(a.scale_id, "–"))]
            for a in s.initial_assessments]
    body = f"<h2>{e(ct(loc, 'std.initial'))}</h2>" + table([ct(loc, "scale"), ct(loc, "within_h"), ct(loc, "std.repeat")], init)
    body += f"<h2>{e(ct(loc, 'std.reposition'))}</h2>" + table(
        [ct(loc, "risk_class"), ct(loc, "every_hours"), ct(loc, "night_every_hours")],
        [[e(ct(loc, "rc." + k)), str(v.every_hours), str(v.night_every_hours)] for k, v in sorted(s.reposition.items())])
    unit = {"glucose": "mg/dl" if ws.care.edition == "de" else "mmol/L", "bp": "mmHg", "pulse": "/min", "temperature": "°C", "spo2": "%"}
    body += f"<h2>{e(ct(loc, 'std.ranges'))}</h2>" + table(
        [ct(loc, "kind"), ct(loc, "low"), ct(loc, "high")],
        [[e(ct(loc, "obs." + k)), e(f"{v.low:g} {unit.get(k, '')}" if v.low is not None else "–"),
          e(f"{v.high:g} {unit.get(k, '')}" if v.high is not None else "–")] for k, v in s.vital_ranges.items()])
    body += f"<h2>{e(ct(loc, 'std.weigh'))} · {e(ct(loc, 'std.fluids'))}</h2>" + kv_table([
        (e(ct(loc, "std.weigh")), e(f"{s.weigh_every_days} d; > {s.weight_loss_percent:g} % / {s.weight_loss_days} d → {ct(loc, 'gp')}")),
        (e(ct(loc, "std.fluids")), e(f"{s.fluid_target_ml} ml; {s.fluid_days_below_inform_gp} d → {ct(loc, 'gp')}"))])
    body += f"<h2>{e(ct(loc, 'std.catalogue'))}</h2>" + table(
        [ct(loc, "measure"), ct(loc, "duration"), ct(loc, "qualification"), ct(loc, "skills")],
        [[e(c.label[loc]), str(c.duration_min), e(ct(loc, "q." + c.qualification.value)),
          e(", ".join(ct(loc, "skill." + k) for k in c.skills))] for c in s.catalogue])
    return ct(loc, "std.title"), "care", body, _side(ws, "standard")


# ---------------------------------------------------------------- daily plan

PW = ("unassigned", "not_on_shift", "qualification", "skill", "same_time", "over_shift_length")


def _warn_text(loc: str, codes: list[str]) -> str:
    seen = []
    for code in codes:
        key = "pw." + code.split(":")[0]
        if key not in seen:
            seen.append(key)
    return " ".join(badge(ct(loc, k), "open" if k == "pw.unassigned" else "warn") for k in seen)


def plan(ws, q: dict[str, str]) -> tuple[str, str, str, str]:
    loc, care, st = _loc(ws), ws.care, ws.state
    from opsdesk.fullcare import tools as care_tools
    token = care_tools.bind(care_tools.CareContext(care, ws.app))
    try:
        now_day, now_shift = current_shift(st)
    finally:
        care_tools.unbind(token)
    try:
        day = date.fromisoformat(q.get("date") or now_day.isoformat())
    except ValueError:
        day = now_day
    shift = q.get("shift") if q.get("shift") in ("early", "late", "night") else now_shift.value
    ward = q.get("ward") or ""
    if ward and ward not in ws.app.unit_names:
        ward = ""
    who = q.get("staff") or ""
    items = dailyplan.items_for(care, day, shift, ward or None)
    staff = dailyplan.on_shift(st, care, day, shift, ward or None)
    warn = dailyplan.warnings(st, care, day, shift, ward or None)
    back = url("/plan", date=day.isoformat(), shift=shift, ward=ward, staff=who)
    load = {s.employee_id: 0 for s in staff}
    for i in items:
        if i.assigned_to in load and i.status != "not_done":
            load[i.assigned_to] += i.duration_min
    shifts = [("early", "Früh" if loc == "de-DE" else "Early"), ("late", "Spät" if loc == "de-DE" else "Late"),
              ("night", "Nacht" if loc == "de-DE" else "Night")]
    picker = form("/plan", text_input("date", ct(loc, "date"), day.isoformat(), kind="date") +
                  select("shift", ct(loc, "shift"), shifts, shift) +
                  select("ward", ct(loc, "unit"), [("", ct(loc, "all"))] + [(w.ward_id, _unit_name(ws, w.ward_id)) for w in st.wards], ward) +
                  select("staff", ct(loc, "staff_member"), [("", ct(loc, "all"))] + [(x.employee_id, x.name) for x in staff], who) +
                  button(ct(loc, "show"), kind="secondary"), method="get", cls="form inline")
    if who:
        items = [i for i in items if i.assigned_to == who]
    staff_rows = [[e(s.name), e(", ".join(ct(loc, "q." + qq) for qq in s.qualifications)),
                   e(", ".join(ct(loc, "skill." + k) for k in s.skills)), f"{load[s.employee_id]} min"] for s in staff]
    body = picker + f"<h2>{e(ct(loc, 'staff_on_shift'))}</h2>" + (table(
        [ct(loc, "name"), ct(loc, "qualification"), ct(loc, "skills"), ct(loc, "load")], staff_rows)
        if staff_rows else f"<p>{e(ct(loc, 'nothing'))}</p>")
    options = [("", ct(loc, "nobody"))] + [(s.employee_id, s.name) for s in staff]
    rows = []
    for i in items:
        res = care.resident(i.resident_id)
        needs = ct(loc, "q." + i.qualification.value) + ("" if not i.skills else " + " + ", ".join(ct(loc, "skill." + k) for k in i.skills))
        if i.status in ("done", "not_done", "handed_over"):
            assigned = e(_staff_name(ws, i.assigned_to))
        else:
            assigned = form("/plan/assign", hidden(item_key=i.item_key, back=back) +
                       select("employee_id", ct(loc, "assigned"), options, i.assigned_to or "", ident=f"a-{i.item_key}") +
                       button(ct(loc, "assign"), kind="secondary"), cls="form inline")
        signed = next((x.by for x in care.plan_state if x.item_key == i.item_key and x.status == "done"), "")
        status = e(ct(loc, "ps." + i.status)) + (f' <span class="hint">{e(i.reason)}</span>' if i.reason else "") + \
            (f' <span class="hint" title="{e(_staff_name(ws, signed))}">({e(_initials(_staff_name(ws, signed)))})</span>' if signed else "")
        status += f' <a href="{e(url("/plan", date=day.isoformat(), shift=shift, ward=ward, item=i.item_key))}">{e(ct(loc, "set_status"))}</a>'
        rows.append([f"{i.due:%H:%M}", e(res.room if res else ""), e(res.name if res else ""), e(i.label), str(i.duration_min),
                     e(needs), assigned, status, _warn_text(loc, warn.get(i.item_key, []))])
    body += f"<h2>{e(ct(loc, 'plan.title'))} · {e(fmt_date(loc, day))} · {e(dict(shifts)[shift])}" + \
        (f" · {e(_staff_name(ws, who))}" if who else "") + "</h2>"
    body += _print_form(ws, None, "daily_plan", back, ref=f"{day.isoformat()}/{shift}" + (f"/{who}" if who else ""))
    body += table([ct(loc, "time"), ct(loc, "room"), ct(loc, "resident"), ct(loc, "measure"), ct(loc, "duration"),
                   ct(loc, "needs"), ct(loc, "assigned"), ct(loc, "status"), ""], rows) if rows else f"<p>{e(ct(loc, 'nothing'))}</p>"
    present = [(r.resident_id, f"{r.room} {r.name}") for r in care.residents if r.status == "present" and (not ward or r.ward_id == ward)]
    cat = [(c.key, c.label[loc]) for c in care.standard.catalogue if c.key not in ("insulin", "round_escort")]
    body += f"<h2>{e(ct(loc, 'add_item'))}</h2>" + form("/plan/add", hidden(back=back) +
        select("resident_id", ct(loc, "resident"), present, required=True) + text_input("date", ct(loc, "date"), day.isoformat(), kind="date") +
        text_input("due", ct(loc, "due_at") + " (HH:MM)", required=True) + select("catalogue_key", ct(loc, "measure"), cat) +
        text_input("reason", ct(loc, "reason"), required=True) + button(ct(loc, "save")))
    key = q.get("item", "")
    item = next((i for i in items if i.item_key == key), None)
    if item is not None:
        res = care.resident(item.resident_id)
        inner = form("/plan/status", hidden(item_key=item.item_key, back=back) +
                     select("status", ct(loc, "mark"), [(s, ct(loc, "ps." + s)) for s in ("done", "not_done", "handed_over", "open")],
                            item.status) + text_input("reason", ct(loc, "reason"), item.reason) + button(ct(loc, "save")))
        body += dialog("item", f"{item.due:%H:%M} · {res.name if res else ''} · {item.label}", inner, back, loc)
    side = f'<ul><li><a href="/plan" class="active" aria-current="page">{e(ct(loc, "plan.title"))}</a></li></ul>'
    return ct(loc, "plan.title"), "plan", body, side
