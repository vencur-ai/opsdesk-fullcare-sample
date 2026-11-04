"""Checks of the OpsDesk FullCare task families (buyer and verifier image).

The scenario builders and reference solutions are not part of this build.
"""

from __future__ import annotations
from collections import Counter
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable
from opsdesk.fullcare import facts, rules
from opsdesk.fullcare.models import Schedule
from opsdesk.fullcare.tools import latest_assessments
Call = Callable[[str, dict[str, Any]], dict[str, Any]]
FAMILIES = ('FC1_admission', 'FC2_assessment_prevention', 'FC3_vital_escalation', 'FC4_round_preparation', 'FC5_doctor_orders', 'FC6_hospital_transfer')

@dataclass
class Ctx:
    """What the checks see: the start and end state of the workspace."""
    world: 'WorldState'
    initial: 'CareState'
    care: 'CareState'
    app: 'WorkforceState'
    initial_app: 'WorkforceState'

    @property
    def today(self):
        return self.world.current_date

    def sent(self):
        old = {m.mail_id for m in self.initial_app.mails}
        return [m for m in self.app.mails if m.folder == 'sent' and m.mail_id not in old]

    def mails_to(self, contact_id):
        c = next((x for x in self.app.contacts if x.contact_id == contact_id), None)
        if c is None:
            return []
        return [m for m in self.sent() if c.address.casefold() in {a.casefold() for a in m.to + m.cc}]

    def mail_with(self, contact_id, needed):
        return any((not facts.missing(f'{m.subject}\n{m.body}', needed) for m in self.mails_to(contact_id)))

    def named(self, text):
        """Residents a free text names: the full name together ("Edna Nowak") or a salutation with the family name
        ("Frau Nowak"). A given name and a family name of two other residents in one list do not name a third
        ("Patricia Taylor, Edna Nowak, Patricia Thompson" names neither Patricia Nowak nor, via "Nowak, Patricia", her)."""
        return {r.resident_id for r in self.care.residents if facts.person(text, r.given_name, r.family_name)}

    def reports_to_head(self):
        """What the nurse reported to the head of nursing, as the residents each report is about: a note (the residents
        ticked; only when none is ticked, the residents named in its text – since 0.6.1, so a note may mention others in a
        "done" part) or a mail to the head of nursing (the residents it names). Natural ways of telling count, not only
        the form's fields."""
        old = {n.escalation_id for n in self.initial.escalations}
        notes = [set(n.resident_ids) or self.named(f'{n.subject}\n{n.text}') for n in self.care.escalations if n.escalation_id not in old]
        return notes + [self.named(f'{m.subject}\n{m.body}') for m in self.mails_to('contact-manager')]

    def active_signatures(self, rid, day):
        return sorted((rules.measure_signature(m) for m in self.care.measures if m.resident_id == rid and rules.active_on(m, day)))
Checks = list[tuple[str, str, bool]]
NOISE = {'room': '^(zimmer|zi\\.?|room|rm\\.?)\\s*(nr\\.?|no\\.?)?\\s*', 'name': '^((frau|herr|herrn|mrs|mr|ms|miss|dr)\\.?\\s+)+'}

def _plain_text(key, value):
    """Master data as typed by a careful person (since 0.6.0): case and spacing do not count, nor the word "Zimmer"/"Room"
    before a room number or a salutation before a name ("Zimmer 12" = "12", "Mrs Mary Smith" = "Mary Smith")."""
    text = re.sub('\\s+', ' ', str(value)).strip().casefold()
    return re.sub(NOISE[key], '', text) if key in NOISE else text

def _same_allergies(got, want):
    """Each allergy of the letter once, with its severity; the recorded substance may say more ("Penicillin V" for
    penicillin) – the letter's substance as a whole word in it. No other allergy."""
    if len(got) != len(want):
        return False
    left = list(got)
    for substance, severity in want:
        hit = next((g for g in left if g[1] == severity and re.search(f'(?<!\\w){re.escape(substance.casefold())}(?!\\w)', g[0].casefold())), None)
        if hit is None:
            return False
        left.remove(hit)
    return True

def _has_obs(ctx, src, same_day=False):
    """The value is recorded – at the time it was measured, or (`same_day`) at any time that day."""
    at = (lambda s: s[:10]) if same_day else lambda s: s
    return any((o.resident_id == src['resident_id'] and o.kind == src['kind'] and (o.value == src['value']) and (o.value2 == src['value2']) and (o.unit == src['unit']) and (at(o.measured_at) == at(src['measured_at'])) for o in ctx.care.observations))

class Admission:
    core = ('resident admitted with name and date of birth',)
    family = 'FC1_admission'

    def check(self, ref, ctx):
        x, m = (ref['expect'], ref['expect']['master'])
        new = [r for r in ctx.care.residents if ctx.initial.resident(r.resident_id) is None]
        r = next((r for r in new if (r.given_name, r.family_name, r.birth_date.isoformat()) == (m['given_name'], m['family_name'], m['birth_date'])), None)
        out: 'Checks' = [('record', 'resident admitted with name and date of birth', r is not None), ('record', 'exactly one new resident', len(new) == 1)]
        if r is None:
            return out
        rid = r.resident_id
        for key in ('sex', 'birth_place', 'ward_id', 'room', 'gp_id', 'care_level', 'funding_note'):
            got = getattr(r, key)
            want = m[key]
            out.append(('record', f'master data: {key}', _plain_text(key, got) == _plain_text(key, want) if want not in (None, '') else got in (None, '')))
        out.append(('record', 'admitted today', r.admitted_on == ctx.today and r.status == 'present'))
        out.append(('record', 'discharge letter filed', any((d.resident_id == rid and d.source_attachment_id == x['attachment_id'] and (d.kind == 'discharge_letter') for d in ctx.care.documents))))
        got_al = [(a.substance, a.severity) for a in ctx.care.allergies if a.resident_id == rid]
        out.append(('record', 'allergies as in the letter', _same_allergies(got_al, x['allergies'])))
        digits = lambda s: ''.join((ch for ch in s if ch.isdigit()))
        out.append(('record', 'next of kin recorded', any((c.resident_id == rid and _plain_text('name', c.name) == _plain_text('name', x['contact']['name']) and (digits(c.phone) == digits(x['contact']['phone'])) for c in ctx.care.contacts))))
        src = [dict(s, resident_id=rid) for s in ref['sources']]
        out += [('values', f"value recorded: {s['kind']}", _has_obs(ctx, s)) for s in src]
        latest = latest_assessments(ctx.care, rid)
        for sid, answers in x['answers'].items():
            out.append(('assessments', f'assessment {sid}', sid in latest and latest[sid].answers == answers))
        tomorrow = ctx.today + timedelta(days=1)
        out.append(('care plan', 'measures per house standard', ctx.active_signatures(rid, tomorrow) == sorted((tuple(s) for s in x['measures']))))
        entries = {e.entry_id for e in ctx.care.careplan if e.resident_id == rid and e.status == 'active'}
        out.append(('care plan', 'every measure belongs to a care-plan entry', bool(entries) and all((mm.entry_id in entries for mm in ctx.care.measures if mm.resident_id == rid))))
        facts_ = [['dob', m['birth_date']] if f[0] == 'dob' else f for f in x['pharmacy_facts']]
        out.append(('mail', 'pharmacy has the medication', ctx.mail_with('care-pharmacy', facts_)))
        if x['mattress']:
            out.append(('mail', 'pressure-relief mattress requested', ctx.mail_with('care-facilities', x['mattress_facts'])))
        return out

class AssessmentPrevention:
    core = ('assessment done with the observed answers',)
    family = 'FC2_assessment_prevention'

    def check(self, ref, ctx):
        x = ref['expect']
        rid = x['resident_id']
        latest = latest_assessments(ctx.care, rid).get(x['scale_id'])
        initial = latest_assessments(ctx.initial, rid).get(x['scale_id'])
        out: 'Checks' = [('assessments', 'assessment done with the observed answers', latest is not None and latest.assessment_id != (initial.assessment_id if initial else None) and (latest.answers == x['answers']))]
        tomorrow = ctx.today + timedelta(days=1)
        out.append(('care plan', 'measures per house standard from tomorrow', ctx.active_signatures(rid, tomorrow) == sorted((tuple(s) for s in x['measures']))))
        entries = {e.entry_id for e in ctx.care.careplan if e.resident_id == rid and e.status == 'active'}
        new = [m for m in ctx.care.measures if m.resident_id == rid and ctx.initial and (m.measure_id not in {y.measure_id for y in ctx.initial.measures})]
        out.append(('care plan', 'new measures belong to a care-plan entry', all((m.entry_id in entries for m in new))))
        if x['mattress']:
            out.append(('mail', 'pressure-relief mattress requested', ctx.mail_with('care-facilities', x['mattress_facts'])))
        return out
VITALS = {'glucose': ('mg/dl', 'mmol/L', 'glucose'), 'bp': ('mmHg', 'mmHg', 'vital_signs'), 'temperature': ('°C', '°C', 'vital_signs'), 'spo2': ('%', '%', 'vital_signs')}

class VitalEscalation:
    core = ('value recorded for',)
    family = 'FC3_vital_escalation'

    def check(self, ref, ctx):
        out: 'Checks' = []
        for it in ref['expect']['items']:
            rid, s = (it['resident_id'], it['source'])
            out.append(('values', f'value recorded for {rid}', _has_obs(ctx, s, same_day=True)))
            out.append(('values', f'recorded with the time it was measured ({rid})', _has_obs(ctx, s)))
            gp_mails = [m for m in ctx.mails_to(it['gp_contact']) if not facts.missing(f'{m.subject}\n{m.body}', it['facts'][:1])]
            extras = [x for x in ctx.care.plan_extra if x.resident_id == rid and ctx.initial and (x.item_id not in {y.item_id for y in ctx.initial.plan_extra})]
            if it['outside']:
                out.append(('mail', f'GP informed with the values ({rid})', ctx.mail_with(it['gp_contact'], it['facts'])))
                obs_ids = {o.obs_id for o in ctx.care.observations if o.resident_id == rid and o.kind == s['kind'] and (o.value == s['value'])}
                values = [f for f in it['facts'] if f[0] == 'value']
                new_reports = [e for e in ctx.care.reports if e.entry_id not in {y.entry_id for y in ctx.initial.reports}]
                out.append(('record', f'care report links the value ({rid})', any((e.resident_id == rid and (set(e.observation_ids) & obs_ids or not facts.missing(e.text, values)) for e in new_reports))))
                due = datetime.strptime(it['follow_due'], '%Y-%m-%d %H:%M')
                ok = [x for x in extras if x.catalogue_key == it['follow_key'] and -10 <= (datetime.combine(x.date, datetime.min.time()).replace(hour=int(x.due[:2]), minute=int(x.due[3:])) - due).total_seconds() / 60 <= 30]
                out.append(('daily plan', f'repeat measurement planned ({rid})', bool(ok)))
                assigned = False
                for x in ok:
                    key = f'{x.item_id}@{x.date.isoformat()}T{x.due}'
                    state = next((p for p in ctx.care.plan_state if p.item_key == key), None)
                    if state and state.assigned_to in ref['eligible'][it['follow_key']]:
                        assigned = True
                out.append(('daily plan', f'repeat measurement given to qualified staff on shift ({rid})', assigned))
            else:
                out.append(('mail', f'no GP mail for a value inside the range ({rid})', not gp_mails))
                out.append(('daily plan', f'no extra measurement for a value inside the range ({rid})', not extras))
        return out

class RoundPreparation:
    core = ("round planned for today at the doctor's time",)
    family = 'FC4_round_preparation'

    def check(self, ref, ctx):
        x = ref['expect']
        plans = [p for p in ctx.care.rounds if p.physician_id == x['physician_id'] and p.date == ctx.today]
        plan = plans[-1] if plans else None
        out: 'Checks' = [('round', "round planned for today at the doctor's time", plan is not None and plan.start == x['start'])]
        if plan is None:
            return out
        out.append(('round', 'residents to be seen', sorted(plan.points) == sorted(x['points'])))
        for rid, pts in x['points'].items():
            out.append(('round', f'points for {rid}', plan.points.get(rid) == pts))
        out.append(('round', 'registered nurse on shift accompanies', plan.escort_id in x['escorts']))
        start = f"{ctx.today.isoformat()} {x['start']}"
        new = [e for e in ctx.app.calendar if e.event_id not in {y.event_id for y in ctx.initial_app.calendar}]
        out.append(('calendar', 'round in the calendar with the nurse', any((e.start == start and plan.escort_id in e.attendees for e in new))))
        out.append(('print', 'round sheet printed', any((j.kind == 'round_sheet' and j.ref == plan.round_id and (j.status != 'cancelled') for j in ctx.care.print_jobs))))
        return out

class DoctorOrders:
    core = ('all new instructions acknowledged',)
    family = 'FC5_doctor_orders'

    def check(self, ref, ctx):
        x = ref['expect']
        rid, tomorrow = (x['resident_id'], ctx.today + timedelta(days=1))
        out: 'Checks' = [('orders', 'all new instructions acknowledged', all(((o := next((y for y in ctx.care.orders if y.order_id == oid), None)) is not None and o.acknowledged_by for oid in x['orders'])))]
        for oid, key, schedule in x['measures']:
            want = rules.exact_signature(key, Schedule.model_validate(schedule))
            out.append(('care plan', f'measure from {oid} with the ordered times', any((m.order_id == oid and rules.active_on(m, tomorrow) and (rules.exact_signature(m.catalogue_key, m.schedule) == want) for m in ctx.care.measures if m.resident_id == rid))))
        if x['stop']:
            out.append(('care plan', 'stopped measure ends', not any((m.resident_id == rid and m.catalogue_key == x['stop'][1] and rules.active_on(m, tomorrow) for m in ctx.care.measures))))
        out.append(('mail', 'pharmacy told about the medication change', ctx.mail_with('care-pharmacy', x['pharmacy_facts'])))
        kept = [m for m in ctx.initial.measures if m.resident_id == rid and rules.active_on(m, tomorrow) and (not x['stop'] or m.measure_id != x['stop'][0])]
        new_kinds = {k for _, k, _ in x['measures']}
        must = Counter((rules.measure_signature(m) for m in kept if m.catalogue_key not in new_kinds))
        must.update((rules.schedule_signature(k, Schedule.model_validate(s)) for _, k, s in x['measures']))
        may = Counter((rules.measure_signature(m) for m in kept if m.catalogue_key in new_kinds))
        active = Counter(ctx.active_signatures(rid, tomorrow))
        out.append(('care plan', 'no other measure changed', not must - active and (not active - must - may)))
        return out

class HospitalTransfer:
    core = ('transfer sheet saved',)
    family = 'FC6_hospital_transfer'

    def check(self, ref, ctx):
        x = ref['expect']
        rid = x['resident_id']
        sheets = [s for s in ctx.care.transfer_sheets if s.resident_id == rid]
        sheet = sheets[-1] if sheets else None
        out: 'Checks' = [('transfer', 'transfer sheet saved', sheet is not None)]
        if sheet is not None:
            for key, want in x['sheet'].items():
                out.append(('transfer', f'sheet: {key}', getattr(sheet, key) == want))
            dest = sheet.destination.casefold()
            out.append(('transfer', 'sheet: hospital', any((w.casefold() in dest for w in x['destination'].split() if len(w) >= 5))))
        jobs = [j for j in ctx.care.print_jobs if j.resident_id == rid and j.status != 'cancelled']
        for kind in x['prints']:
            ok = any((j.kind == kind and (kind != 'transfer_sheet' or (sheet is not None and j.ref == sheet.sheet_id)) for j in jobs))
            out.append(('print', f'printed: {kind}', ok))
        for cid in x['relatives']:
            out.append(('mail', f'relative informed ({cid})', ctx.mail_with(cid, x['relative_facts'])))
        r = ctx.care.resident(rid)
        if x['planned']:
            out.append(('record', 'still in the home until tomorrow', r.status == 'present'))
            out.append(('mail', 'transport booked with name, date, time and how', ctx.mail_with('care-transport', x['transport_facts'])))
            out.append(('record', 'hospital appointment tomorrow at the admission time, with transport', any((a.resident_id == rid and a.date.isoformat() == x['tomorrow'] and (a.start == '10:00') and (a.kind == 'hospital') and a.transport_needed and (a.status == 'planned') for a in ctx.care.appointments))))
        else:
            out.append(('record', 'status: in hospital', r.status == 'in_hospital'))
        return out
REGISTRY = {f.family: f for f in (Admission(), AssessmentPrevention(), VitalEscalation(), RoundPreparation(), DoctorOrders(), HospitalTransfer())}
from opsdesk.fullcare.families3 import FAMILIES_P3, REGISTRY_P3
REGISTRY.update(REGISTRY_P3)
FAMILIES_ALL = FAMILIES + FAMILIES_P3
from opsdesk.fullcare.longshift import FAMILY as LONG_FAMILY, EarlyShift
REGISTRY[LONG_FAMILY] = EarlyShift()
FAMILIES_LONG = (LONG_FAMILY,)
