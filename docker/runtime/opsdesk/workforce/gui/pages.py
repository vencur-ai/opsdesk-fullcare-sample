"""Screens of OpsDesk Workforce. Each function returns (title, nav key, body HTML) for one GET request.

Reads use the pure functions in `views.py` on the live state; nothing here changes state.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from opsdesk.core.models import AgencyRequestStatus, Qualification, ShiftType
from opsdesk.workforce import views
from opsdesk.workforce.gui.html import (
    badge, button, checkbox, dialog, e, form, hidden, kv_table, markdown, page_number, pager, paginate, select, table, text_input,
    textarea, url,
)
from opsdesk.workforce.i18n import (
    council_reason, fmt_date, fmt_hours, fmt_money, fmt_short, qual_name, rule_text, shift_name, t,
)

QUALS = ("registered_nurse", "team_lead", "care_assistant")


def _num(loc: str, value: float) -> str:
    """Contract hours as people write them (37.5 / 37,5)."""
    text = f"{value:g}"
    return text.replace(".", ",") if loc == "de-DE" else text


class Ctx:
    def __init__(self, ws):
        self.ws, self.st, self.app, self.loc = ws, ws.state, ws.app, ws.app.locale
        self.emp = {x.employee_id: x for x in self.st.employees}

    def tr(self, key: str) -> str:
        return t(self.loc, key)

    def name(self, employee_id: str) -> str:
        x = self.emp.get(employee_id)
        return x.name if x else employee_id

    def short(self, employee_id: str) -> str:
        first, _, last = self.name(employee_id).partition(" ")
        return f"{first} {last[:1]}." if last else first

    def unit(self, ward_id: str) -> str:
        return self.app.unit_names.get(ward_id, ward_id)

    def quals(self, employee_id: str) -> str:
        x = self.emp.get(employee_id)
        if not x:
            return ""
        held = {v.value for v in x.qualifications}
        top = next(q for q in QUALS if q in held)  # registered nurses also hold the care-assistant skill
        return f'<span class="tag{" rn" if top == "registered_nurse" else ""}">{e(t(self.loc, "qualshort." + top))}</span>'

    def shift_label(self, d: date, ward_id: str, shift: str) -> str:
        return f"{fmt_short(self.loc, d)} · {self.unit(ward_id)} · {shift_name(self.loc, shift)}"

    def unit_options(self, with_all: bool = True) -> list[tuple[str, str]]:
        opts = [("", self.tr("all"))] if with_all else []
        return opts + [(w.ward_id, self.unit(w.ward_id)) for w in sorted(self.st.wards, key=lambda w: w.ward_id)]

    def staff_options(self, with_all: bool = True) -> list[tuple[str, str]]:
        opts = [("", self.tr("all"))] if with_all else []
        return opts + [(x.employee_id, x.name) for x in sorted(self.st.employees, key=lambda x: (x.name, x.employee_id))]

    def shift_options(self) -> list[tuple[str, str]]:
        return [(s.value, shift_name(self.loc, s.value)) for s in views.SHIFTS]

    def date_options(self) -> list[tuple[str, str]]:
        return [(d.isoformat(), fmt_date(self.loc, d)) for d in views.horizon_dates(self.st)]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _iso_week(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _parse_week(value: str | None) -> date | None:
    try:
        y, w = value.split("-W")
        return date.fromisocalendar(int(y), int(w), 1)
    except Exception:
        return None


# ------------------------------------------------------------------------------------------- rota

def rota(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    st, loc = c.st, c.loc
    horizon = views.horizon_dates(st)
    first, last = horizon[0], horizon[-1]
    default_view = "month" if st.horizon_days <= 7 else "week"  # 3.1.1: two weeks do not fit 1280 px
    view = q.get("view") if q.get("view") in ("week", "month") else default_view
    unit = q.get("unit") or ""
    qual = q.get("qual") or ""
    staff = q.get("staff") or ""
    if view == "month":
        days = horizon
        week = ""
    else:
        monday = _parse_week(q.get("week")) or _monday(st.current_date)
        monday = min(max(monday, _monday(first)), _monday(last))
        days = [monday + timedelta(days=i) for i in range(7)]
        week = _iso_week(monday)
    inside = [d for d in days if first <= d <= last]
    cells = views.grid(st, inside, unit or None)
    base = dict(view=view if view != default_view else None, week=week or None, unit=unit, qual=qual, staff=staff)
    published = st.roster.published
    n = views.numbers(st)
    warn_count = len(views.warnings(st))
    cards = (f'<div class="cards"><div class="card"><div class="muted">{e(c.tr("period"))}</div><div class="big">'
             f'{e(fmt_short(loc, first))} – {e(fmt_short(loc, last))}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("status"))}</div><div class="big">'
             f'{badge(c.tr("published"), "ok") if published else badge(c.tr("draft"), "warn")}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("open_positions"))}</div><div class="big">{n["open_positions"]}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("warnings"))}</div><div class="big">{warn_count}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("planned_cost"))}</div><div class="big">'
             f'{e(fmt_money(loc, n["total_cost_cents"]))}</div></div></div>')
    toolbar = form("/rota", (
        f'<div class="field">{select("unit", c.tr("unit"), c.unit_options(), unit)}</div>'
        f'<div class="field">{select("view", c.tr("view"), [("week", c.tr("week")), ("month", c.tr("period"))], view, ident="f-view")}</div>'
        + (f'<div class="field">{select("week", c.tr("week"), [(_iso_week(_monday(first) + timedelta(days=7 * i)), _iso_week(_monday(first) + timedelta(days=7 * i)) + " · " + fmt_short(loc, _monday(first) + timedelta(days=7 * i))) for i in range((_monday(last) - _monday(first)).days // 7 + 1)], week)}</div>' if view == "week" else "")
        + f'<div class="field">{select("qual", c.tr("qualification"), [("", c.tr("all"))] + [(x, qual_name(loc, x)) for x in QUALS], qual)}</div>'
        f'<div class="field">{select("staff", c.tr("employee"), c.staff_options(), staff)}</div>'
        f'<div class="field">{button(c.tr("filter"), kind="secondary")}</div><div class="spacer"></div>'
        + (_week_links(c, days, first, last, base) if view == "week" else "")
    ), method="get", cls="toolbar")
    head = [e(c.tr("unit")) + " / " + e(c.tr("shift"))] + [
        f'{e(fmt_short(loc, d))}{" · " + e(c.tr("today")) if d == st.current_date else ""}' for d in days]
    rows = []
    for ward in sorted(st.wards, key=lambda w: w.ward_id):
        if unit and ward.ward_id != unit:
            continue
        for shift in views.SHIFTS:
            a, b = views.shift_times(shift)
            line = [f'<th scope="row" class="rowhead">{e(c.unit(ward.ward_id))}<br>{e(shift_name(loc, shift.value))} '
                    f'<span class="muted">{a}–{b}</span></th>']
            for d in days:
                if not first <= d <= last:
                    line.append('<td class="out">–</td>')
                    continue
                line.append(_cell(c, cells[(d, ward.ward_id, shift)], d, ward.ward_id, shift, base, qual, staff))
            rows.append(line)
    totals = [f'<th scope="row">{e(c.tr("total"))}</th>']
    for d in days:
        if not first <= d <= last:
            totals.append('<td class="out"></td>')
            continue
        day_cells = [v for k, v in cells.items() if k[0] == d]
        shifts = sum(len(v["assignments"]) for v in day_cells)
        opens = sum(1 for v in day_cells if v["open"])
        cost = sum(views.shift_cost_cents(st, a) for v in day_cells for a in v["assignments"])
        totals.append(f'<td class="meta">{e(c.tr("shifts"))}: {shifts}<br>{e(c.tr("open").capitalize())}: {opens}<br>'
                      f'{e(fmt_money(loc, cost))}</td>')
    grid_html = (f'<table class="grid rota{" compact" if len(days) > 7 else ""}"><thead><tr>' + "".join(f'<th scope="col">{h}</th>' for h in head)
                 + '</tr></thead><tbody>' + "".join("<tr>" + "".join(
                     x if x.startswith(("<td", "<th")) else f"<td>{x}</td>" for x in r) + "</tr>" for r in rows)
                 + "<tr>" + "".join(totals) + "</tr></tbody></table>")
    body = cards + toolbar + grid_html + '<p class="muted">' + e(
        "Tagessumme ohne Überstundenzuschläge; Wochenzuschläge sind in den geplanten Kosten enthalten." if loc == "de-DE"
        else "Daily totals exclude weekly overtime premiums; those are included in the planned cost.") + "</p>"
    cell = q.get("cell")
    if cell:
        body += _shift_dialog(c, cell, base)
    return c.tr("nav.rota"), "rota", body


def _week_links(c: Ctx, days: list[date], first: date, last: date, base: dict) -> str:
    out = []
    prev_monday, next_monday = days[0] - timedelta(days=7), days[0] + timedelta(days=7)
    if prev_monday + timedelta(days=6) >= first:
        out.append(f'<a class="btn secondary" href="{e(url("/rota", **{**base, "week": _iso_week(prev_monday)}))}">'
                   f'‹ {e(c.tr("previous"))}</a>')
    if next_monday <= last:
        out.append(f'<a class="btn secondary" href="{e(url("/rota", **{**base, "week": _iso_week(next_monday)}))}">'
                   f'{e(c.tr("next"))} ›</a>')
    return '<div class="field">' + " ".join(out) + "</div>"


def _cell(c: Ctx, cell: dict, d: date, ward_id: str, shift: ShiftType, base: dict, qual: str, staff: str) -> str:
    loc = c.loc
    slot = cell["demand"]
    staff_rows = cell["assignments"]
    shown = [a for a in staff_rows if (not staff or a.employee_id == staff) and (
        not qual or qual in {v.value for v in c.emp[a.employee_id].qualifications})]
    names = "".join(f'<li title="{e(c.name(a.employee_id))}">{e(c.short(a.employee_id))}{c.quals(a.employee_id)}</li>'
                    for a in shown)
    if cell["agency"]:
        names += f'<li>{e(c.tr("agency_cover"))} ×{cell["agency"]}<span class="tag ag">{e(c.tr("agency"))}</span></li>'
    meta = ""
    if slot is not None:
        have = len(staff_rows) + cell["agency"]
        rn_need = slot.min_qualifications.get(Qualification.RN, 0)
        rn_have = sum(1 for a in staff_rows if Qualification.RN in c.emp[a.employee_id].qualifications)
        meta = (f'<div class="meta"><span class="nowrap">{have}/{slot.min_headcount}</span> · <span class="nowrap">'
                f'{e(t(loc, "qualshort.registered_nurse"))} {rn_have}/{rn_need}</span></div>')
    flags = ""
    if cell["open"]:
        g = cell["open"]
        # the scarcest role first on screen (nurse, team leader, carer); the claim format keeps its own order
        missing = next((q for q in QUALS if g["missing_qualifications"].get(q)), g["missing"])
        qual_missing = g["missing_qualifications"].get(missing, 0) if missing != "headcount" else 0
        count = max(g["missing_headcount"], qual_missing, 1)  # people missing in total, of whom qual_missing must be RN
        if missing == "headcount":  # any qualified person will do
            text = f'{count} {c.tr("open")}'
        elif qual_missing < count:
            what = t(loc, "qualshort." + missing)
            text = (f'{count} {c.tr("open")} (davon {qual_missing} {what})' if loc == "de-DE" else
                    f'{count} {c.tr("open")} (incl. {qual_missing} {what})')
        else:
            text = f'{count} {c.tr("open")} – {t(loc, "qualshort." + missing)}'
        flags += badge(text, "open")
    if cell["warnings"]:
        flags += " " + badge(f'⚠ {len(cell["warnings"])}', "warn")
    key = f"{d.isoformat()}.{shift.value}.{ward_id}"
    label = f"{c.shift_label(d, ward_id, shift.value)}"
    today = ' class="today"' if d == c.st.current_date else ""
    return (f'<td{today}><a class="cell" aria-label="{e(label)}" '
            f'href="{e(url("/rota", **{**base, "cell": key}))}"><ul class="names">{names}</ul>{meta}{flags}</a></td>')


def remove_confirm(ws, q: dict[str, str]) -> tuple[str, str, str]:
    """3.1: "Remove from shift" asks first; the confirmation posts the same form with `confirmed`."""
    c = Ctx(ws)
    a = next((x for x in c.st.roster.assignments if x.assignment_id == q.get("assignment_id")), None)
    back = q.get("back") or "/rota"
    if a is None:
        return c.tr("nav.rota"), "rota", f'<p class="empty">{e(c.tr("empty.not_found"))}</p>'
    text = c.tr("remove.confirm").format(name=c.name(a.employee_id), shift=c.shift_label(a.date, a.ward_id, a.shift_type.value))
    inner = (f"<p>{e(text)}</p>" + form("/rota/unassign", hidden(assignment_id=a.assignment_id, back=back, confirmed="1",
                                                                notify=q.get("notify") or None)
                                       + button(c.tr("remove"), kind="danger")
                                       + f' <a class="btn secondary" href="{e(back)}">{e(c.tr("cancel"))}</a>', cls="inline"))
    return c.tr("nav.rota"), "rota", dialog("remove-dialog", c.tr("remove.title"), inner, back, c.loc)


def _warning_text(c: Ctx, w: dict, d: date) -> str:
    """Rule text; for availability, name the actual reason (absence type, fixed day off)."""
    if w["rule_id"] == "house.employee_availability":
        absence = next((a for a in c.st.absences if a.employee_id == w["staff_id"] and a.start_date <= d <= a.end_date), None)
        if absence is not None:
            return f'{t(c.loc, "absence." + absence.absence_type.value)} {c.tr("until")} {fmt_short(c.loc, absence.end_date)}'
        # a night shift runs into the next morning
        nxt = d + timedelta(days=1)
        absence = next((a for a in c.st.absences if a.employee_id == w["staff_id"] and a.start_date <= nxt <= a.end_date), None)
        if absence is not None:
            return f'{t(c.loc, "absence." + absence.absence_type.value)} {c.tr("from_day")} {fmt_short(c.loc, nxt)}'
        person = c.emp.get(w["staff_id"])
        if person is not None and d.weekday() in person.fixed_days_off:
            from opsdesk.workforce.i18n import WEEKDAYS_LONG
            return f'{c.tr("fixed_day_off")} {WEEKDAYS_LONG[c.loc][d.weekday()]}'
        if person is not None and person.night_only:
            return c.tr("nights_only")
    return rule_text(c.loc, w["rule_id"])


def _shift_dialog(c: Ctx, key: str, base: dict) -> str:
    st, loc = c.st, c.loc
    try:
        day_s, shift_s, ward_id = key.split(".", 2)
        d, shift = date.fromisoformat(day_s), ShiftType(shift_s)
    except Exception:
        return ""
    if not any(w.ward_id == ward_id for w in st.wards):
        return ""
    cell = views.grid(st, [d], ward_id).get((d, ward_id, shift))
    if cell is None:
        return ""
    back = url("/rota", **base, cell=key)
    close = url("/rota", **base)
    a_t, b_t = views.shift_times(shift)
    slot = cell["demand"]
    parts = [f'<p><strong>{e(fmt_date(loc, d))}</strong> · {e(c.unit(ward_id))} · {e(shift_name(loc, shift.value))} '
             f'{a_t}–{b_t}</p>']
    if slot is not None:
        # Registered nurses and team leaders also hold the care-assistant skill, so only the scarcer ones are listed.
        need = ", ".join(f"{c.tr('at_least')} {n} {t(loc, ('qualpl.' if n != 1 else 'qualone.') + q.value)}" for q, n in sorted(
            slot.min_qualifications.items(), key=lambda x: QUALS.index(x[0].value)) if n and q.value != "care_assistant")
        parts.append(f'<p>{e(c.tr("demand"))}: {slot.min_headcount} {e(c.tr("people"))}{" (" + e(need) + ")" if need else ""}'
                     + (f' · {badge(c.tr("open"), "open")}' if cell["open"] else f' · {badge(c.tr("covered"), "ok")}')
                     + (f' · {e(c.tr("agency_cover"))}: {cell["agency"]}' if cell["agency"] else "") + "</p>")
    on_rows = []
    same_day = [a for a in st.roster.assignments if a.date == d and not (a.ward_id == ward_id and a.shift_type == shift)]
    for a in cell["assignments"]:
        warn = "; ".join(dict.fromkeys(_warning_text(c, w, d) for w in cell["warnings"] if w["staff_id"] == a.employee_id))
        swap_opts = [(o.assignment_id, f"{c.name(o.employee_id)} · {c.unit(o.ward_id)} · {shift_name(loc, o.shift_type.value)}")
                     for o in sorted(same_day, key=lambda o: (o.ward_id, o.shift_type.value, c.name(o.employee_id)))]
        remove = form("/rota/unassign", hidden(assignment_id=a.assignment_id, back=back)
                      + checkbox("notify", c.tr("notify_short"), True, ident=f"n-r-{a.assignment_id}")
                      + " " + button(c.tr("remove"), kind="danger"), cls="inline")
        swap = (f'<details><summary>{e(c.tr("swap_with"))} …</summary>' + form(
            "/rota/swap", hidden(first_assignment_id=a.assignment_id, back=back)
            + select("second_assignment_id", c.tr("swap_with"), swap_opts, ident=f"s-{a.assignment_id}")
            + " " + button(c.tr("swap_with"), kind="secondary"), cls="inline") + "</details>") if swap_opts else ""
        on_rows.append([e(c.name(a.employee_id)) + c.quals(a.employee_id),
                        badge(warn, "warn") if warn else e("–"), remove + swap])
    parts.append(f'<h3>{e(c.tr("on_shift"))}</h3>' + (table([c.tr("name"), c.tr("warnings"), ""], on_rows)
                                                        if on_rows else f'<p class="empty">{e(c.tr("empty.shift"))}</p>'))
    rows = []
    for row in views.candidates(st, d, ward_id, shift):
        if row["status"] == "on_shift":
            continue
        reasons = []
        for r in row["reasons"]:
            if r["kind"] == "absence":
                note = next((m.note for m in c.app.absence_meta if m.absence_id == r.get("absence_id") and m.note
                             and m.channel == "planned"), "")
                reasons.append(note or f'{t(loc, "absence." + r["absence_type"])} {c.tr("until")} {fmt_short(loc, r["until"])}')
            elif r["kind"] == "overlap":
                reasons.append(f'{shift_name(loc, r["shift"])} · {c.unit(r["unit"])}')
            elif r["kind"] == "rule":
                reasons.append(rule_text(loc, r["rule_id"]))
        prof = c.app.profile(row["employee_id"])
        x = c.emp[row["employee_id"]]
        monday = _monday(d)
        week_min = sum(views.shift_work_minutes(a) for a in st.roster.assignments
                       if a.employee_id == x.employee_id and monday <= a.date < monday + timedelta(days=7))
        hours = f'{fmt_hours(loc, week_min)} / {_num(loc, x.contract.hours_per_week)} {c.tr("hours_week")}'
        wishes = "; ".join(prof.wishes) if prof else ""
        kind = {"available": "ok", "warning": "warn", "busy": "", "absent": "err"}[row["status"]]
        action = ""
        if row["status"] in ("available", "warning"):
            action = form("/rota/assign", hidden(employee_id=x.employee_id, date=d.isoformat(), ward_id=ward_id,
                                                 shift_type=shift.value, back=back)
                          + checkbox("notify", c.tr("notify_short"), True, ident=f"n-a-{x.employee_id}")
                          + " " + button(c.tr("assign"), kind="primary"), cls="inline")
        rows.append([e(x.name) + c.quals(x.employee_id), badge(c.tr("status." + row["status"]), kind),
                     e("; ".join(reasons)), f'<span class="nowrap">{e(hours)}</span>', e(wishes), action])
    parts.append(f'<h3>{e(c.tr("who_can"))}</h3>' + table(
        [c.tr("name"), c.tr("status"), c.tr("reason"), c.tr("profile.hours"), c.tr("profile.availability"), ""], rows))
    parts.append(f'<p><a href="{e(url("/agency", date=d.isoformat()))}">{e(c.tr("nav.agency"))} – '
                 f'{e(fmt_short(loc, d))}</a></p>')
    return dialog("shift-dialog", c.shift_label(d, ward_id, shift.value), "".join(parts), close, loc)


# ------------------------------------------------------------------------------------------- staff

def staff(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    term = (q.get("q") or "").strip().casefold()
    qual = q.get("qual") or ""
    people = sorted(c.st.employees, key=lambda x: (x.name, x.employee_id))
    out = []
    for x in people:
        prof = c.app.profile(x.employee_id)
        if term and term not in x.name.casefold() and term not in (prof.staff_number.casefold() if prof else ""):
            continue
        if qual and qual not in {v.value for v in x.qualifications}:
            continue
        out.append((x, prof))
    rows, page_no, pages = paginate(out, page_number(q))
    toolbar = form("/staff", f'<div class="field">{text_input("q", c.tr("search"), q.get("q") or "", kind="search")}</div>'
                   f'<div class="field">{select("qual", c.tr("qualification"), [("", c.tr("all"))] + [(v, qual_name(loc, v)) for v in QUALS], qual)}</div>'
                   f'<div class="field">{button(c.tr("search"), kind="secondary")}</div>', method="get", cls="toolbar")
    lines = []
    for x, prof in rows:
        nxt = min((a for a in c.st.roster.assignments if a.employee_id == x.employee_id), key=lambda a: (a.date, a.shift_type.value),
                  default=None)
        lines.append([f'<a href="/staff/{e(x.employee_id)}">{e(x.name)}</a>', e(prof.staff_number if prof else ""),
                      e(_role(loc, {q_.value for q_ in x.qualifications})),
                      e(f'{t(loc, "contract." + x.contract.employment_type.value)} · {_num(loc, x.contract.hours_per_week)} {c.tr("hours_week")}'),
                      e(c.shift_label(nxt.date, nxt.ward_id, nxt.shift_type.value) if nxt else "–"),
                      e(fmt_hours(loc, views.planned_minutes(c.st, x.employee_id)))])
    body = toolbar + table([c.tr("name"), c.tr("staff_number"), c.tr("qualification"), c.tr("contract"),
                            "Nächster Dienst" if loc == "de-DE" else "Next shift", c.tr("profile.planned")], lines) \
        + pager(loc, "/staff", page_no, pages, len(out), q=q.get("q"), qual=qual)
    return c.tr("nav.staff"), "staff", body


def profile(ws, employee_id: str, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    x = c.emp.get(employee_id)
    if x is None:
        return c.tr("nav.staff"), "staff", f'<p class="empty">{e(c.tr("empty.not_found"))}</p>'
    prof = c.app.profile(employee_id)
    persona = next((p for p in st.staff_personas if p.employee_id == employee_id), None)
    quals = {v.value for v in x.qualifications}
    facts = [(c.tr("staff_number"), prof.staff_number if prof else ""),
             (c.tr("qualification"), _role(loc, quals, also=True)),
             (c.tr("contract"), f'{t(loc, "contract." + x.contract.employment_type.value)} · {_num(loc, x.contract.hours_per_week)} {c.tr("hours_week")}'),
             (c.tr("profile.rate"), fmt_money(loc, int(x.hourly_rate * 100))),
             (c.tr("profile.phone"), prof.phone if prof else ""),
             (c.tr("profile.hired"), fmt_date(loc, prof.hire_date, weekday=False) if prof else "")]
    if x.is_night_worker:  # a legal status (night-work limits), not a wish: shown with the contract facts
        facts.append((c.tr("profile.status"), c.tr("night_worker")))
    avail = []
    if x.fixed_days_off:
        from opsdesk.workforce.i18n import WEEKDAYS_LONG
        avail.append(f'{c.tr("fixed_day_off")} ' + ", ".join(WEEKDAYS_LONG[loc][i] for i in sorted(x.fixed_days_off)))
    if x.night_only:
        avail.append(c.tr("nights_only"))
    avail.append(f'{c.tr("max_nights")}: {x.max_nights_per_month}')
    if x.weekly_hours_opt_out:
        avail.append(c.tr("opt_out"))
    avail += prof.wishes if prof else []
    shifts = sorted((a for a in st.roster.assignments if a.employee_id == employee_id), key=lambda a: (a.date, a.shift_type.value))
    absences = sorted((a for a in st.absences if a.employee_id == employee_id), key=lambda a: a.start_date)
    planned = views.planned_minutes(st, employee_id)
    body = ('<div class="cols"><div class="panel"><h2>' + e(c.tr("profile.overview")) + "</h2>"
            + kv_table((e(k), e(v)) for k, v in facts) + "</div>"
            + '<div class="panel"><h2>' + e(c.tr("profile.qualifications")) + "</h2>"
            + table([c.tr("qualification"), c.tr("profile.valid_until")],
                    [[e(k), e(fmt_date(loc, v, weekday=False))] for k, v in sorted((prof.qualification_valid_until if prof else {}).items())])
            + "</div></div>"
            + '<div class="cols"><div class="panel"><h2>' + e(c.tr("profile.availability")) + "</h2><ul>"
            + "".join(f"<li>{e(v)}</li>" for v in avail) + "</ul></div>"
            + '<div class="panel"><h2>' + e(c.tr("profile.hours")) + "</h2>"
            + kv_table([(e(c.tr("profile.planned")), e(fmt_hours(loc, planned))),
                        (e(c.tr("contract")), e(f'{_num(loc, x.contract.hours_per_week)} {c.tr("hours_week")}')),
                        (e(c.tr("profile.previous")), e(fmt_hours(loc, prof.previous_period_minutes if prof else 0))),
                        (e(c.tr("profile.balance")), e(fmt_hours(loc, prof.hours_balance_minutes if prof else 0)))])
            + "</div></div>"
            + '<div class="panel"><h2>' + e(c.tr("profile.shifts")) + "</h2>"
            + (table([c.tr("date"), c.tr("unit"), c.tr("shift")],
                     [[e(fmt_date(loc, a.date)), e(c.unit(a.ward_id)), e(shift_name(loc, a.shift_type.value))] for a in shifts])
               if shifts else f'<p class="empty">{e(c.tr("empty.no_shifts"))}</p>') + "</div>"
            + '<div class="panel"><h2>' + e(c.tr("nav.absences")) + "</h2>"
            + (table([c.tr("type"), c.tr("from"), c.tr("to")],
                     [[e(t(loc, "absence." + a.absence_type.value)), e(fmt_date(loc, a.start_date)), e(fmt_date(loc, a.end_date))]
                      for a in absences]) if absences else f'<p class="empty">{e(c.tr("empty.no_absences"))}</p>') + "</div>"
            + '<div class="panel"><h2>' + e(c.tr("profile.edit")) + "</h2>"
            + form(f"/staff/{employee_id}/edit",
                   '<div class="row"><div>' + text_input("phone", c.tr("profile.phone"), prof.phone if prof else "") + "</div>"
                   + "<div>" + text_input("contract_hours_per_week", c.tr("hours_week"), f"{x.contract.hours_per_week:g}", kind="number",
                                          extra='step="0.5" min="1" max="60"') + "</div></div>"
                   + '<div class="row">' + "".join(checkbox("qualifications", qual_name(loc, v), v in quals, ident=f"q-{v}", value=v)
                                                    for v in QUALS) + "</div>"
                   + textarea("notes", c.tr("profile.notes"), prof.notes if prof else "") + button(c.tr("save"))) + "</div>")
    return x.name, "staff", body


# ------------------------------------------------------------------------------------------- absences

def absences(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    kind = q.get("type") or ""
    items = sorted((a for a in st.absences if not kind or a.absence_type.value == kind),
                   key=lambda a: (a.start_date, c.name(a.employee_id), a.absence_id))
    rows, page_no, pages = paginate(items, page_number(q))
    meta = {m.absence_id: m for m in c.app.absence_meta}
    lines = []
    for a in rows:
        m = meta.get(a.absence_id)
        lines.append([f'<a href="/staff/{e(a.employee_id)}">{e(c.name(a.employee_id))}</a>',
                      e(t(loc, "absence." + a.absence_type.value)), e(fmt_date(loc, a.start_date)), e(fmt_date(loc, a.end_date)),
                      e(m.entered_by if m else ""), e(fmt_date(loc, m.entered_on, weekday=False) if m else ""),
                      e(t(loc, "channel." + m.channel)) if m else "",
                      e(m.note if m else ""),
                      f'<a href="/absences/{e(a.absence_id)}/edit">{e(c.tr("edit"))}</a> · '
                      f'<a href="/absences/{e(a.absence_id)}/delete">{e(c.tr("delete"))}</a>'])
    types = [("", c.tr("all"))] + [(v, t(loc, "absence." + v)) for v in ("sick", "holiday", "other")]
    toolbar = form("/absences", f'<div class="field">{select("type", c.tr("type"), types, kind)}</div>'
                   f'<div class="field">{button(c.tr("filter"), kind="secondary")}</div><div class="spacer"></div>'
                   f'<div class="field"><a class="btn" href="/absences/new">{e(c.tr("new_absence"))}</a></div>',
                   method="get", cls="toolbar")
    body = toolbar + (table([c.tr("employee"), c.tr("type"), c.tr("from"), c.tr("to"), c.tr("entered_by"),
                             c.tr("entered_on"), c.tr("source"), c.tr("note"), ""], lines) if lines else
                      f'<p class="empty">{e(c.tr("empty.absences"))}</p>') + pager(loc, "/absences", page_no, pages, len(items), type=kind)
    return c.tr("nav.absences"), "absences", body


def absence_form(ws, absence_id: str | None) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    a = next((x for x in c.st.absences if x.absence_id == absence_id), None) if absence_id else None
    meta = next((m for m in c.app.absence_meta if m.absence_id == absence_id), None) if absence_id else None
    types = [(v, t(loc, "absence." + v)) for v in ("sick", "holiday", "other")]
    inner = ('<div class="row">'
             + (f'<div>{select("employee_id", c.tr("employee"), c.staff_options(False), "", required=True)}</div>' if a is None
                else f'<div><label>{e(c.tr("employee"))}</label><strong>{e(c.name(a.employee_id))}</strong></div>')
             + f'<div>{select("absence_type", c.tr("type"), types, a.absence_type.value if a else "sick")}</div>'
             + f'<div>{text_input("start_date", c.tr("from"), a.start_date.isoformat() if a else c.st.current_date.isoformat(), kind="date", required=True)}</div>'
             + f'<div>{text_input("end_date", c.tr("to"), a.end_date.isoformat() if a else c.st.current_date.isoformat(), kind="date", required=True)}</div></div>'
             + textarea("note", c.tr("note"), meta.note if meta else "") + button(c.tr("save")))
    action = f"/absences/{absence_id}/edit" if a else "/absences/new"
    title = c.tr("edit") if a else c.tr("new_absence")
    return f'{c.tr("nav.absences")} – {title}', "absences", form(action, inner)


def absence_delete(ws, absence_id: str) -> tuple[str, str, str]:
    c = Ctx(ws)
    a = next((x for x in c.st.absences if x.absence_id == absence_id), None)
    if a is None:
        return c.tr("nav.absences"), "absences", f'<p class="empty">{e(c.tr("empty.not_found"))}</p>'
    text = (f"{c.name(a.employee_id)} · {t(c.loc, 'absence.' + a.absence_type.value)} · {fmt_date(c.loc, a.start_date)} – "
            f"{fmt_date(c.loc, a.end_date)}")
    inner = (f"<p>{e(text)}</p>" + form(f"/absences/{absence_id}/delete", button(c.tr("delete"), kind="danger")
                                       + f' <a class="btn secondary" href="/absences">{e(c.tr("cancel"))}</a>'))
    return c.tr("nav.absences"), "absences", dialog("delete-dialog", c.tr("delete") + "?", inner, "/absences", c.loc)


# ------------------------------------------------------------------------------------------- requests

def requests(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    by_id = {a.assignment_id: a for a in c.st.roster.assignments}
    rows = []
    for r in c.app.requests:
        a1, a2 = _request_shifts(c, r, by_id)
        rows.append([f'<a href="/requests/{e(r.request_id)}">{e(c.name(r.requester_id))}</a>',
                     e(c.shift_label(a1.date, a1.ward_id, a1.shift_type.value) if a1 else "–"),
                     e(c.name(r.partner_id)), e(c.shift_label(a2.date, a2.ward_id, a2.shift_type.value) if a2 else "–"),
                     badge(c.tr("req." + r.status), {"open": "warn", "approved": "ok", "declined": "err", "withdrawn": ""}[r.status])
                     + (" " + badge(c.tr("req.awaiting").format(name=_first_name(c.name(r.partner_id))), "")
                        if r.status == "open" and not r.partner_confirmed else ""),
                     e(r.reason)])
    body = (table([c.tr("requested_by"), c.tr("gives"), c.tr("partner"), c.tr("takes"), c.tr("status"), c.tr("reason")], rows,
                  caption=c.tr("swap_request")) if rows else f'<p class="empty">{"Keine Anfragen." if loc == "de-DE" else "No requests."}</p>')
    return c.tr("nav.requests"), "requests", body


def _role(loc: str, held: set[str], also: bool = False) -> str:
    """The person's grade as a planner reads it ("Registered nurse"), not the list of skills the rules use;
    the profile adds that nurses and team leaders can also cover care-assistant shifts."""
    top = next((q for q in QUALS if q in held), "")
    text = qual_name(loc, top) if top else "–"
    if also and top != "care_assistant" and "care_assistant" in held:
        text += " · kann Pflegehilfe-Dienste übernehmen" if loc == "de-DE" else " · can cover care-assistant shifts"
    return text


def _first_name(name: str) -> str:
    return name.split()[0] if name else name


def _request_shifts(c: Ctx, r, by_id):
    first, second = by_id.get(r.first_assignment_id), by_id.get(r.second_assignment_id)
    if r.status == "approved":
        return (second, first) if second and second.employee_id == r.requester_id else (first, second)
    return (first, second) if first and first.employee_id == r.requester_id else (second, first)


def request_detail(ws, request_id: str) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    r = next((x for x in c.app.requests if x.request_id == request_id), None)
    if r is None:
        return c.tr("nav.requests"), "requests", f'<p class="empty">{e(c.tr("empty.not_found"))}</p>'
    by_id = {a.assignment_id: a for a in st.roster.assignments}
    own, other = _request_shifts(c, r, by_id)
    msg = next((m for m in st.messages if m.message_id == r.message_id), None)
    lines = [[e(c.name(r.requester_id)) + c.quals(r.requester_id), e(c.shift_label(own.date, own.ward_id, own.shift_type.value) if own else "–"),
              e(c.shift_label(other.date, other.ward_id, other.shift_type.value) if other else "–")],
             [e(c.name(r.partner_id)) + c.quals(r.partner_id), e(c.shift_label(other.date, other.ward_id, other.shift_type.value) if other else "–"),
              e(c.shift_label(own.date, own.ward_id, own.shift_type.value) if own else "–")]]
    body = ((f'<div class="panel"><div class="muted">{e(t(loc, "channel.app"))} · {e(fmt_date(loc, msg.sent_on))}</div>'
             f'<p>{e(msg.body)}</p></div>' if msg else "")
            + table([c.tr("employee"), c.tr("gives"), c.tr("takes")], lines)
            + f'<p>{c.tr("status")}: {badge(c.tr("req." + r.status), {"open": "warn", "approved": "ok", "declined": "err", "withdrawn": ""}[r.status])}'
            + (f" · {e(r.reason)}" if r.reason else "")
            + (" · " + e(c.tr("req.awaiting" if not r.partner_confirmed else "req.confirmed").format(name=c.name(r.partner_id)))
               if r.status == "open" and (not r.partner_confirmed or any(x.action in ("Swap confirmed", "Tausch bestätigt")
                                                                          and x.user == c.name(r.partner_id) for x in c.app.session_log))
               else "") + "</p>")
    if r.status == "open":
        body += ('<div class="cols"><div class="panel">' + form(f"/requests/{request_id}/decide",
                 hidden(decision="approve") + textarea("reason", c.tr("reason"), "", ident="r-approve", rows=2)
                 + checkbox("notify", c.tr("notify_both"), True, ident="n-approve") + "<br>" + button(c.tr("approve")))
                 + '</div><div class="panel">' + form(f"/requests/{request_id}/decide",
                 hidden(decision="decline") + textarea("reason", c.tr("decline_reason"), "", ident="r-decline", rows=2, required=True)
                 + checkbox("notify", c.tr("notify_both"), True, ident="n-decline") + "<br>" + button(c.tr("decline"), kind="danger"))
                 + "</div></div>")
    return c.tr("swap_request"), "requests", body


# ------------------------------------------------------------------------------------------- messages

def messages(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    box = "sent" if q.get("box") == "sent" else "inbox"
    meta = {m.message_id: m for m in c.app.message_meta}
    if box == "inbox":  # mails are in OpsDesk Mail (3.1.1); here: phone notes and the staff app
        items = [m for m in st.messages if m.recipient_id == "manager" and getattr(meta.get(m.message_id), "channel", "") != "email"]
    else:
        items = [m for m in st.messages if m.sender_id == "manager"]
    items = sorted(items, key=lambda m: (m.sent_on, m.message_id), reverse=True)
    rows, page_no, pages = paginate(items, page_number(q))
    lines = []
    for m in rows:
        mm = meta.get(m.message_id)
        channel = t(loc, "channel." + (mm.channel if mm else ("app" if box == "inbox" else "email")))
        who = mm.sender_label if mm else (c.name(m.sender_id) if box == "inbox" else c.name(m.recipient_id))
        link = f'<a href="/messages/{e(m.message_id)}">{e(m.subject)}</a>'
        unread = box == "inbox" and not m.is_read
        lines.append([e(fmt_date(loc, m.sent_on)), e(channel), f"<strong>{e(who)}</strong>" if unread else e(who),
                      f"<strong>{link}</strong>" if unread else link])
    tabs = (f'<div class="toolbar"><a class="btn {"" if box == "inbox" else "secondary"}" href="/messages">{e(c.tr("inbox"))}</a>'
            f'<a class="btn {"" if box == "sent" else "secondary"}" href="/messages?box=sent">{e(c.tr("sent"))}</a>'
            f'<div class="spacer"></div><a class="btn" href="/messages/new">{e(c.tr("new_message"))}</a></div>')
    body = tabs + (table([c.tr("date"), c.tr("channel"), c.tr("from") if box == "inbox" else c.tr("recipient"), c.tr("subject")], lines)
                   if lines else f'<p class="empty">{e(c.tr("empty.messages"))}</p>') + pager(loc, "/messages", page_no, pages, len(items),
                                                                    box=box if box == "sent" else None)
    return c.tr("nav.messages"), "messages", body


def message_detail(ws, message_id: str) -> tuple[str, str, str]:
    c = Ctx(ws)
    m = next((x for x in c.st.messages if x.message_id == message_id), None)
    if m is None:
        return c.tr("nav.messages"), "messages", f'<p class="empty">{e(c.tr("empty.not_found"))}</p>'
    mm = next((x for x in c.app.message_meta if x.message_id == message_id), None)
    sender = mm.sender_label if mm else (c.app.home.user_name if m.sender_id == "manager" else c.name(m.sender_id))
    recipient = c.app.home.user_name if m.recipient_id == "manager" else c.name(m.recipient_id)
    reply = ""
    if m.sender_id in c.emp:
        reply = f'<p><a class="btn secondary" href="{e(url("/messages/new", to=m.sender_id, subject="Re: " + m.subject))}">' \
                f'{e("Antworten" if c.loc == "de-DE" else "Reply")}</a></p>'
    body = (f'<div class="panel"><div class="muted">{e(fmt_date(c.loc, m.sent_on))} · {e(c.tr("from"))}: {e(sender)} · '
            f'{e(c.tr("recipient"))}: {e(recipient)}</div><h2>{e(m.subject)}</h2><div class="doc">{e(m.body)}</div></div>' + reply)
    return c.tr("nav.messages"), "messages", body


def message_new(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    inner = ('<div class="row"><div>' + select("recipient_id", c.tr("recipient"), c.staff_options(False), q.get("to", ""), required=True)
             + "</div><div>" + text_input("subject", c.tr("subject"), q.get("subject", ""), required=True, extra='size="50"')
             + "</div></div>" + textarea("body", c.tr("message"), "", required=True, rows=5)
             + f'<fieldset class="panel"><legend>{e(c.tr("about_shift"))}</legend><div class="row"><div>'
             + select("shift_date", c.tr("date"), [("", "–")] + c.date_options(), q.get("date", "")) + "</div><div>"
             + select("ward_id", c.tr("unit"), [("", "–")] + c.unit_options(False), q.get("unit", "")) + "</div><div>"
             + select("shift_type", c.tr("shift"), [("", "–")] + c.shift_options(), q.get("shift", "")) + "</div></div>"
             + checkbox("request_intent", c.tr("ask_to_work"), False) + "</fieldset>" + button(c.tr("send")))
    return c.tr("new_message"), "messages", form("/messages/send", inner)


# ------------------------------------------------------------------------------------------- agency

def agency(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    n = views.numbers(st)
    budget = n["agency_budget_cents"]
    cards = (f'<div class="cards"><div class="card"><div class="muted">{e(c.tr("budget"))}</div><div class="big">{e(fmt_money(loc, budget))}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("budget_used"))}</div><div class="big">{e(fmt_money(loc, n["agency_spend_cents"]))}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("budget_left"))}</div><div class="big">{e(fmt_money(loc, budget - n["agency_spend_cents"]))}</div></div></div>')
    if c.app.budget_in_mail:  # the home manager sets the budget by mail; the booking page only shows what is used
        cards = (f'<div class="cards"><div class="card"><div class="muted">{e(c.tr("budget"))}</div><div>{e(c.tr("budget_by_manager"))}</div></div>'
                 f'<div class="card"><div class="muted">{e(c.tr("budget_used"))}</div><div class="big">{e(fmt_money(loc, n["agency_spend_cents"]))}</div></div></div>')
    offer_rows = []
    for o in sorted(st.agency_offers, key=lambda o: (o.available_from, o.agency_name, o.offer_id)):
        days = [d for d in views.horizon_dates(st) if o.available_from <= d <= o.available_until]
        req = form("/agency/request", hidden(offer_id=o.offer_id)
                   + select("requested_for", c.tr("date"), [(d.isoformat(), fmt_short(loc, d)) for d in days],
                            q.get("date", ""), ident=f"d-{o.offer_id}")
                   + select("ward_id", c.tr("unit"), c.unit_options(False), ident=f"w-{o.offer_id}")
                   + select("shift_type", c.tr("shift"), c.shift_options(), ident=f"s-{o.offer_id}")
                   + text_input("staff_count", c.tr("staff_count"), "1", kind="number", ident=f"c-{o.offer_id}",
                                extra=f'min="1" max="{o.max_staff}"')
                   + " " + button(c.tr("request_cover")), cls="inline") if days else "–"
        offer_rows.append([e(o.agency_name), e(qual_name(loc, o.qualification.value)),
                           e(f"{fmt_short(loc, o.available_from)} – {fmt_short(loc, o.available_until)}"),
                           e(fmt_money(loc, int(o.hourly_rate * 100))), str(o.max_staff), str(o.lead_time_days), req])
    booking_rows = []
    for r in sorted(st.agency_requests, key=lambda r: (r.requested_for, r.request_id)):
        cancel = form(f"/agency/{r.request_id}/cancel", button(c.tr("cancel_booking"), kind="danger"), cls="inline") \
            if r.status == AgencyRequestStatus.CONFIRMED else ""
        booking_rows.append([e(c.shift_label(r.requested_for, r.ward_id, r.shift_type.value)), e(qual_name(loc, r.qualification.value)),
                             str(r.staff_count), e(fmt_money(loc, int(r.estimated_cost * 100))),
                             badge(c.tr("confirmed" if r.status == AgencyRequestStatus.CONFIRMED else "cancelled"),
                                   "ok" if r.status == AgencyRequestStatus.CONFIRMED else ""), cancel])
    body = (cards + f'<h2>{e(c.tr("offers"))}</h2>'
            + (table([c.tr("agency"), c.tr("qualification"), c.tr("available"), c.tr("rate"), c.tr("max_staff"), c.tr("lead_time"), ""],
                     offer_rows) if offer_rows else f'<p class="empty">{"Keine Angebote im Zeitraum." if loc == "de-DE" else "No offers for this period."}</p>')
            + f'<h2>{e(c.tr("bookings"))}</h2>'
            + (table([c.tr("shift"), c.tr("qualification"), c.tr("staff_count"), c.tr("est_cost"), c.tr("status"), ""], booking_rows)
               if booking_rows else f'<p class="empty">{e(c.tr("empty.bookings"))}</p>'))
    return c.tr("nav.agency"), "agency", body


# ------------------------------------------------------------------------------------------- publish

def changed_positions(ws) -> list[tuple[str, date, str, str]]:
    def keys(roster):
        return {(a.employee_id, a.date, a.ward_id, a.shift_type.value) for a in roster.assignments}
    return sorted(keys(ws.initial.roster) ^ keys(ws.state.roster), key=lambda k: (k[1], k[3], k[2], k[0]))


def notified_positions(ws) -> set[tuple[str, str, str, str]]:
    out = set()
    for tool, args, ok in ws.call_log:
        if tool != "messaging.send" or not ok or not isinstance(args, dict):
            continue
        if all(args.get(k) for k in ("recipient_id", "shift_date", "shift_type", "ward_id")) and not args.get("request_intent"):
            out.add((args["recipient_id"], str(args["shift_date"]), args["ward_id"], args["shift_type"]))
    return out


def publish(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    n = views.numbers(st)
    opens = views.open_positions(st)
    warns = views.warnings(st)
    changed = changed_positions(ws)
    told = notified_positions(ws)
    status = badge(c.tr("published"), "ok") if st.roster.published else badge(c.tr("draft"), "warn")
    cards = (f'<div class="cards"><div class="card"><div class="muted">{e(c.tr("status"))}</div><div class="big">{status}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("open_positions"))}</div><div class="big">{n["open_positions"]}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("warnings"))}</div><div class="big">{len(warns)}</div></div>'
             f'<div class="card"><div class="muted">{e(c.tr("planned_cost"))}</div><div class="big">{e(fmt_money(loc, n["total_cost_cents"]))}</div></div></div>')
    open_rows = [[e(c.shift_label(g["date"], g["unit"], g["shift"].value)),
                  e(qual_name(loc, g["missing"]) if g["missing"] != "headcount" else t(loc, "qual.headcount"))] for g in opens]
    warn_rows = [[e(fmt_short(loc, w["date"]) if w["date"] else ""), e(c.name(w["staff_id"]) if w["staff_id"] else "–"),
                  e(_warning_text(c, w, w["date"]) if w["date"] else rule_text(loc, w["rule_id"]))] for w in warns]
    changed_rows = [[e(c.name(k[0])), e(c.shift_label(k[1], k[2], k[3])),
                     badge("✓", "ok") if (k[0], k[1].isoformat(), k[2], k[3]) in told else "–"] for k in changed]
    body = (cards + f'<h2>{e(c.tr("open_positions"))}</h2>'
            + (table([c.tr("shift"), c.tr("report.missing")], open_rows) if open_rows else f'<p class="empty">{e(c.tr("report.none_open"))}</p>')
            + f'<h2>{e(c.tr("warnings"))}</h2>'
            + (table([c.tr("date"), c.tr("employee"), c.tr("warning")], warn_rows) if warn_rows else f'<p class="empty">{e(c.tr("no_warnings"))}</p>')
            + f'<h2>{e(c.tr("publish.changed"))}</h2>'
            + (table([c.tr("employee"), c.tr("shift"), "✉"], changed_rows) if changed_rows else f'<p class="empty">{e(c.tr("empty.changes"))}</p>'))
    if st.roster.published:
        body = f'<p>{badge(c.tr("publish.is_published"), "ok")}</p>' + body
    else:
        body += f'<p><a class="btn" href="/publish?confirm=1">{e(c.tr("publish.review"))}</a></p>'
        if q.get("confirm"):
            inner = (f'<p>{e(c.tr("publish.confirm"))}</p><p class="muted">{n["open_positions"]} {e(c.tr("open_positions"))} · '
                     f'{len(warns)} {e(c.tr("warnings"))}</p>'
                     + form("/publish", checkbox("notify", c.tr("publish.notify"), True) + "<br>" + button(c.tr("publish.button"))
                            + f' <a class="btn secondary" href="/publish">{e(c.tr("cancel"))}</a>'))
            body += dialog("publish-dialog", c.tr("publish.title"), inner, "/publish", loc)
    return c.tr("publish.title"), "publish", body


# ------------------------------------------------------------------------------------------- reports

def understaffing_new(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    opens = views.open_positions(st)
    active = c.app.active_report()
    rows = []
    for i, g in enumerate(opens):
        missing_opts = [(v, qual_name(loc, v)) for v in QUALS] + [("headcount", t(loc, "qual.headcount"))]
        # nothing is ticked for the planner: each shift is reported only when someone decides it cannot be covered
        rows.append([checkbox(f"include_{i}", c.tr("report.include"), False, ident=f"inc-{i}"),
                     e(c.shift_label(g["date"], g["unit"], g["shift"].value))
                     + hidden(**{f"date_{i}": g["date"].isoformat(), f"ward_{i}": g["unit"], f"shift_{i}": g["shift"].value}),
                     select(f"missing_{i}", c.tr("report.missing"), missing_opts, g["missing"], ident=f"m-{i}"),
                     text_input(f"reason_{i}", c.tr("reason"), "", ident=f"why-{i}", extra='size="40"')])
    extra = ('<fieldset class="panel"><legend>' + e(c.tr("report.extra")) + '</legend><div class="row"><div>'
             + select("date_x", c.tr("date"), [("", "–")] + c.date_options(), "") + "</div><div>"
             + select("ward_x", c.tr("unit"), c.unit_options(False)) + "</div><div>"
             + select("shift_x", c.tr("shift"), c.shift_options()) + "</div><div>"
             + select("missing_x", c.tr("report.missing"), [(v, qual_name(loc, v)) for v in QUALS] + [("headcount", t(loc, "qual.headcount"))])
             + "</div><div>" + text_input("reason_x", c.tr("reason"), "", extra='size="30"') + "</div></div></fieldset>")
    inner = (f'<p>{e(c.tr("report.intro"))}</p>'
             + (table([c.tr("report.include"), c.tr("shift"), c.tr("report.missing"), c.tr("reason")], rows) if rows
                else f'<p class="empty">{e(c.tr("report.none_open"))}</p>')
             + hidden(count=len(opens)) + extra + textarea("note", c.tr("note"), "") + button(c.tr("report.send")))
    body = ""
    if active:
        body += (f'<div class="panel"><strong>{e(c.tr("report.active"))}</strong> · {e(c.tr("report.sent_on"))} '
                 f'{e(fmt_date(loc, active.sent_on))} · {len(active.items)} {e(c.tr("shifts"))} '
                 + form(f"/reports/{active.report_id}/withdraw", button(c.tr("report.withdraw"), kind="secondary"), cls="inline")
                 + "</div>")
    body += form("/reports/understaffing", inner)
    return c.tr("report.title"), "understaffing", body


def reports(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    rep_rows = [[e(r.report_id), e(fmt_date(loc, r.sent_on)), str(len(r.items)),
                 badge(c.tr("report.withdrawn"), "") if r.withdrawn else badge(c.tr("report.active"), "warn")]
                for r in reversed(c.app.reports)]
    hours_rows = []
    weeks = max(1, st.horizon_days / 7)
    for x in sorted(st.employees, key=lambda x: (x.name, x.employee_id)):
        planned = views.planned_minutes(st, x.employee_id)
        contract = int(x.contract.hours_per_week * 60 * weeks)
        hours_rows.append([e(x.name), e(fmt_hours(loc, planned)), e(fmt_hours(loc, contract)), e(fmt_hours(loc, planned - contract))])
    page_rows, page_no, pages = paginate(hours_rows, page_number(q))
    per_unit = Counter()
    for a in st.roster.assignments:
        per_unit[a.ward_id] += views.shift_cost_cents(st, a)
    unit_rows = [[e(c.unit(w.ward_id)), e(fmt_money(loc, per_unit[w.ward_id]))] for w in sorted(st.wards, key=lambda w: w.ward_id)]
    n = views.numbers(st)
    premiums = n["total_cost_cents"] - sum(per_unit.values()) - n["agency_spend_cents"]
    if premiums:
        unit_rows.append([f'<em>{e(c.tr("reports.premiums"))}</em>', e(fmt_money(loc, premiums))])
    if n["agency_spend_cents"]:
        unit_rows.append([e(c.tr("reports.agency")), e(fmt_money(loc, n["agency_spend_cents"]))])
    unit_rows.append([f'<strong>{e(c.tr("total"))}</strong>', f'<strong>{e(fmt_money(loc, n["total_cost_cents"]))}</strong>'])
    body = (f'<h2>{e(c.tr("nav.understaffing"))}</h2>'
            + (table(["#", c.tr("report.sent_on"), c.tr("shifts"), c.tr("status")], rep_rows) if rep_rows else f'<p class="empty">{e(c.tr("empty.reports"))}</p>')
            + f'<h2>{e(c.tr("reports.cost_unit"))}</h2>' + table([c.tr("unit"), c.tr("planned_cost")], unit_rows)
            + f'<h2>{e(c.tr("reports.hours"))}</h2>'
            + table([c.tr("employee"), c.tr("profile.planned"), c.tr("contract"), "Δ"], page_rows)
            + pager(loc, "/reports", page_no, pages, len(hours_rows)))
    return c.tr("nav.reports"), "reports", body


# ------------------------------------------------------------------------------------------- audit, documents, P1

def audit(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    term = (q.get("object") or "").strip().casefold()
    entries = [x for x in list(reversed(c.app.session_log)) + list(reversed(c.app.history))
               if not term or term in x.object.casefold() or term in x.action.casefold()]
    rows, page_no, pages = paginate(entries, page_number(q))
    lines = [[e(f"{fmt_date(loc, date.fromisoformat(x.at[:10]), weekday=False)} {x.at[11:]}"), e(x.user), e(x.action),
              e(x.object), e(x.before), e(x.after)] for x in rows]
    toolbar = form("/audit", f'<div class="field">{text_input("object", c.tr("search"), q.get("object") or "", kind="search")}</div>'
                   f'<div class="field">{button(c.tr("filter"), kind="secondary")}</div>', method="get", cls="toolbar")
    body = toolbar + table([c.tr("audit.time"), c.tr("audit.user"), c.tr("audit.action"), c.tr("audit.object"),
                            c.tr("audit.before"), c.tr("audit.after")], lines) \
        + pager(loc, "/audit", page_no, pages, len(entries), object=q.get("object"))
    return c.tr("nav.audit"), "audit", body


def documents(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    rows = [[f'<a href="/documents/{e(d.document_id)}">{e(d.title)}</a>',
             e(f"{fmt_date(loc, d.effective_from, weekday=False)} – {fmt_date(loc, d.effective_until, weekday=False) if d.effective_until else '…'}"),
             badge(c.tr("documents.expired"), "err") if d.effective_until and d.effective_until < c.st.current_date
             else badge(c.tr("documents.valid"), "ok")]
            for d in sorted(c.st.documents, key=lambda d: d.title)]
    return c.tr("nav.documents"), "documents", table([c.tr("documents.title"), c.tr("documents.valid"), c.tr("status")], rows)


def document_detail(ws, document_id: str) -> tuple[str, str, str]:
    c = Ctx(ws)
    d = next((x for x in c.st.documents if x.document_id == document_id), None)
    if d is None:
        return c.tr("nav.documents"), "documents", f'<p class="empty">{e(c.tr("empty.not_found"))}</p>'
    valid = (f'{c.tr("documents.valid")}: {fmt_date(c.loc, d.effective_from, weekday=False)} – '
             f'{fmt_date(c.loc, d.effective_until, weekday=False) if d.effective_until else "…"}')
    return d.title, "documents", f'<p class="muted">{e(valid)}</p><div class="doc rendered">{markdown(d.body)}</div>'


def timeclock(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc = c.loc
    who = q.get("employee") or ""
    items = sorted((x for x in c.st.timeclock_entries if not who or x.employee_id == who),
                   key=lambda x: (x.shift_date, c.name(x.employee_id), x.entry_id), reverse=True)
    rows, page_no, pages = paginate(items, page_number(q))
    lines = [[e(fmt_date(loc, x.shift_date)), e(c.name(x.employee_id)), e(x.clock_in.strftime("%H:%M")),
              e(x.clock_out.strftime("%H:%M")),
              form("/timeclock/correct", hidden(entry_id=x.entry_id) + button(c.tr("timeclock.correct"), kind="secondary"), cls="inline")]
             for x in rows]
    toolbar = form("/timeclock", f'<div class="field">{select("employee", c.tr("employee"), c.staff_options(), who)}</div>'
                   f'<div class="field">{button(c.tr("filter"), kind="secondary")}</div>', method="get", cls="toolbar")
    body = toolbar + (table([c.tr("date"), c.tr("employee"), c.tr("timeclock.in"), c.tr("timeclock.out"), ""], lines)
                      if lines else f'<p class="empty">{e(c.tr("empty.timeclock"))}</p>') + pager(loc, "/timeclock", page_no, pages, len(items), employee=who)
    return c.tr("nav.timeclock"), "timeclock", body


def payroll(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    days = views.horizon_dates(st)
    create = form("/payroll/export", '<div class="row"><div>' + text_input("start_date", c.tr("from"), days[0].isoformat(), kind="date")
                  + "</div><div>" + text_input("end_date", c.tr("to"), days[-1].isoformat(), kind="date") + "</div><div>"
                  + button(c.tr("payroll.create")) + "</div></div>")
    parts = [f'<p>{e(c.tr("payroll.intro"))}</p>', create]
    if not st.payroll_exports:
        parts.append(f'<p class="empty">{e(c.tr("payroll.none"))}</p>')
    for x in reversed(st.payroll_exports):
        rows = [[e(c.name(r.employee_id)), e(fmt_hours(loc, r.rostered_minutes)), e(fmt_hours(loc, r.clocked_minutes)),
                 e(fmt_hours(loc, r.payable_minutes)),
                 badge(fmt_hours(loc, r.discrepancy_minutes), "warn") if r.discrepancy_minutes else e(fmt_hours(loc, 0))] for r in x.rows]
        parts.append(table([c.tr("employee"), c.tr("payroll.hours_rostered"), c.tr("payroll.hours_clocked"),
                            c.tr("payroll.hours_payable"), c.tr("payroll.discrepancy")], rows,
                           caption=f"{fmt_date(loc, x.start_date, weekday=False)} – {fmt_date(loc, x.end_date, weekday=False)}"))
    return c.tr("nav.payroll"), "payroll", "".join(parts)


def council(ws, q: dict[str, str]) -> tuple[str, str, str]:
    c = Ctx(ws)
    loc, st = c.loc, c.st
    head = f'<p>{e(c.tr("council.required") if st.works_council_required else c.tr("council.not_required"))}</p>'
    latest = st.council_submissions[-1] if st.council_submissions else None
    state = (badge(c.tr("council." + latest.status.value), "ok" if latest.status.value == "approved" else "err") if latest
             else badge(c.tr("council.not_submitted"), "warn" if st.works_council_required else ""))
    agreement = next((d for d in sorted(st.documents, key=lambda d: d.document_id) if "agreement" in d.document_id), None)
    cards = (f'<div class="cards"><div class="card"><div class="muted">{e(c.tr("council.status"))}</div><div class="big">{state}</div></div>'
             + (f'<div class="card"><div class="muted">{e(c.tr("council.agreement"))}</div><div><a href="/documents/'
                f'{e(agreement.document_id)}">{e(agreement.title)}</a></div></div>' if agreement else "") + "</div>")
    rows = [[e(fmt_date(loc, s.submitted_on)), badge(c.tr("council." + s.status.value), "ok" if s.status.value == "approved" else "err"),
             "<br>".join(e(council_reason(loc, r, c.app.unit_names)) for r in s.reasons) or "–"] for s in reversed(st.council_submissions)]
    submit = form("/council/submit", button(c.tr("council.submit"))) if st.works_council_required else ""  # n2 (3.1.1)
    body = (head + cards + submit + f'<h2>{e(c.tr("council.history"))}</h2>'
            + (table([c.tr("date"), c.tr("status"), c.tr("reason")], rows) if rows else f'<p class="empty">{e(c.tr("council.none"))}</p>'))
    if q.get("confirm"):
        inner = (f'<p>{e(c.tr("council.confirm"))}</p>' + form("/council/submit", hidden(confirmed="1") + button(c.tr("council.submit"))
                                                               + f' <a class="btn secondary" href="/council">{e(c.tr("cancel"))}</a>'))
        body += dialog("council-dialog", c.tr("council.submit"), inner, "/council", loc)
    return c.tr("nav.council"), "council", body
