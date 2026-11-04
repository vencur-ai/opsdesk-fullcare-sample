"""Checks of the OpsDesk FullCare task families (buyer and verifier image).

The scenario builders and reference solutions are not part of this build.
"""

from __future__ import annotations
from datetime import date, timedelta
from opsdesk.fullcare import dailyplan, facts, rules
from opsdesk.fullcare.tools import latest_assessments
FAMILIES_P3 = ('FC7_hospital_return', 'FC8_lab_results', 'FC9_fluid_balance', 'FC10_plan_repair', 'FC11_relative_request', 'FC12_inspection_prep', 'FC13_cannot_be_done')
CONSENT_WORDS = ['schweigepflicht', 'entbindung', 'entbunden', 'einwilligung', 'einverständnis', 'freigabe', 'vollmacht', 'consent', 'permission', 'authoris', 'authoriz', 'release']

def _check_plan_change(ctx, x, out):
    rid, tomorrow = (x['resident_id'], ctx.today + timedelta(days=1))
    out.append(('care plan', 'measures per house standard from tomorrow', ctx.active_signatures(rid, tomorrow) == sorted((tuple(s) for s in x['measures']))))
    entries = {e.entry_id for e in ctx.care.careplan if e.resident_id == rid and e.status == 'active'}
    new = [m for m in ctx.care.measures if m.resident_id == rid and m.measure_id not in {y.measure_id for y in ctx.initial.measures}]
    out.append(('care plan', 'new measures belong to a care-plan entry', all((m.entry_id in entries for m in new))))
    if x.get('mattress'):
        out.append(('mail', 'pressure-relief mattress requested', ctx.mail_with('care-facilities', _name_facts_of(ctx, rid) + [['word', 'matratze', 'mattress']])))

def _worked(state):
    """Long tier: the colleague the item was given to did it (ticked off by them, not by the nurse)."""
    return state.status == 'done' and bool(state.by) and (state.by == state.assigned_to)

def _name_facts_of(ctx, rid):
    r = ctx.care.resident(rid)
    return [['name', r.given_name, r.family_name]]

class HospitalReturn:
    core = ('back in the home',)
    family = 'FC7_hospital_return'

    def check(self, ref, ctx):
        x = ref['expect']
        rid = x['resident_id']
        out: 'Checks' = [('record', 'back in the home', ctx.care.resident(rid).status == 'present')]
        out.append(('record', 'discharge letter filed', any((d.resident_id == rid and d.source_attachment_id == x['attachment_id'] and (d.kind == 'discharge_letter') for d in ctx.care.documents))))
        out.append(('record', 'new intolerance recorded', any((a.resident_id == rid and a.substance.casefold() == x['allergy'][0].casefold() and (a.severity == x['allergy'][2]) for a in ctx.care.allergies))))
        latest, initial = (latest_assessments(ctx.care, rid), latest_assessments(ctx.initial, rid))
        for sid, answers in x['answers'].items():
            fresh = sid in latest and (sid not in initial or latest[sid].assessment_id != initial[sid].assessment_id)
            out.append(('assessments', f'assessment {sid} after the return', fresh and latest[sid].answers == answers))
        _check_plan_change(ctx, x, out)
        out.append(('mail', 'pharmacy told about the change', ctx.mail_with('care-pharmacy', x['pharmacy_facts'])))
        return out
ANALYTES = {'hb': ('Hämoglobin', 'Haemoglobin', 'g/dl', 'g/L', (12.0, 16.0), (120, 160)), 'wbc': ('Leukozyten', 'White cells', '/nl', 'x10^9/L', (4.0, 10.0), (4.0, 10.0)), 'crea': ('Kreatinin', 'Creatinine', 'mg/dl', 'µmol/L', (0.5, 1.1), (45, 90)), 'na': ('Natrium', 'Sodium', 'mmol/l', 'mmol/L', (135, 145), (133, 146)), 'k': ('Kalium', 'Potassium', 'mmol/l', 'mmol/L', (3.5, 5.1), (3.5, 5.3))}

class LabResults:
    core = ('lab report filed',)
    family = 'FC8_lab_results'

    def check(self, ref, ctx):
        x = ref['expect']
        rid = x['resident_id']
        out: 'Checks' = [('record', 'lab report filed', any((d.resident_id == rid and d.source_attachment_id == x['attachment_id'] and (d.kind == 'lab_report') for d in ctx.care.documents)))]
        inform = x.get('inform', 'outside_normal_range')
        told = lambda needed: ctx.mail_with(x['gp_contact'], needed)
        if inform == 'always':
            out.append(('mail', 'GP gets every lab report (house standard)' + (' with the values outside the normal range' if x['flagged'] else ''), told(x['gp_facts'])))
        elif inform == 'outside_normal_range' and x['flagged']:
            out.append(('mail', 'GP told the values outside the normal range', told(x['gp_facts'])))
        else:
            out.append(('mail', 'no GP mail ' + ('when every value is normal' if inform != 'never' else '(house standard: the GP reads it himself)'), not any((not facts.missing(f'{m.subject}\n{m.body}', _name_facts_of(ctx, rid)) for m in ctx.mails_to(x['gp_contact'])))))
        return out

class FluidBalance:
    core = ("today's drinks recorded",)
    family = 'FC9_fluid_balance'

    def check(self, ref, ctx):
        x = ref['expect']
        rid, tomorrow = (x['resident_id'], ctx.today + timedelta(days=1))
        out: 'Checks' = [('values', "today's drinks recorded", all((any((f.resident_id == rid and f.ml == s['ml'] and (str(f.at)[:10] == str(s['at'])[:10]) for f in ctx.care.fluids)) for s in x['fluid_sources']))), ('values', 'each drink with its time', all((any((f.resident_id == rid and f.ml == s['ml'] and (f.at == s['at']) for f in ctx.care.fluids)) for s in x['fluid_sources'])))]
        new = [e for e in ctx.care.reports if e.resident_id == rid and e.entry_id not in {y.entry_id for y in ctx.initial.reports}]
        out.append(('record', 'balance of the last days in the daily notes', any((not facts.missing(e.text, x['report_facts']) for e in new))))
        if x['inform']:
            out.append(('mail', 'GP informed with the daily amounts', ctx.mail_with(x['gp_contact'], x['gp_facts'])))
        else:
            out.append(('mail', 'no GP mail below the house threshold', not any((not facts.missing(f'{m.subject}\n{m.body}', _name_facts_of(ctx, rid)) for m in ctx.mails_to(x['gp_contact'])))))
        offers = [rules.measure_signature(m) for m in ctx.care.measures if m.resident_id == rid and m.catalogue_key == 'fluid_offer' and rules.active_on(m, tomorrow)]
        if x['offer_needed']:
            out.append(('care plan', 'drinks offered as per standard', tuple(x['offer_signature']) in offers))
        else:
            out.append(('care plan', 'no extra drinks plan when the balance is fine', len(offers) == len([m for m in ctx.initial.measures if m.resident_id == rid and m.catalogue_key == 'fluid_offer' and rules.active_on(m, tomorrow)])))
        return out

class PlanRepair:
    core = ('nothing left with the colleague who is off sick',)
    family = 'FC10_plan_repair'

    def check(self, ref, ctx):
        x = ref['expect']
        today = ctx.today
        state = {s.item_key: s for s in ctx.care.plan_state}
        out: 'Checks' = [('rota', 'sick call recorded', any((a.employee_id == x['sick'] and a.start_date <= today <= a.end_date and (a.absence_type.value == 'sick') for a in ctx.world.absences)))]
        out.append(('daily plan', 'nothing left with the colleague who is off sick', all((state.get(k) is not None and state[k].assigned_to != x['sick'] for k in x['items']))))
        staff = {s.employee_id: s for s in dailyplan.on_shift(ctx.world, ctx.care, today, 'early')}
        items = {i.item_key: i for i in dailyplan.items_for(ctx.care, today, 'early')}
        out.append(('daily plan', 'every item given to qualified staff on shift', all((k in items and items[k].assigned_to in staff and (not dailyplan.eligibility(items[k], staff[items[k].assigned_to])) for k in x['items']))))
        out.append(('daily plan', 'other assignments unchanged', all((state.get(k) is not None and state[k].assigned_to == who for k, who in x['before'].items() if k not in x['items']))))
        out.append(('daily plan', 'no item marked done or dropped', all((state.get(k) is None or state[k].status == 'open' or _worked(state[k]) for k in x['items']))))
        return out

class RelativeRequest:
    core = ('the relative got an answer',)
    family = 'FC11_relative_request'

    def check(self, ref, ctx):
        x = ref['expect']
        rid = x['resident_id']
        out: 'Checks' = [('mail', 'the relative got an answer', bool(ctx.mails_to(x['contact'])))]
        out.append(('mail', 'answer confirms the visit' + ('' if x['allowed'] else ' and explains that consent is missing'), ctx.mail_with(x['contact'], x['reply_facts'])))
        out.append(('record', 'visit entered', any((a.resident_id == rid and a.kind == 'relatives' and (a.date.isoformat() == x['visit']) and (a.start == x['hour']) and (a.status == 'planned') for a in ctx.care.appointments))))
        return out

class InspectionPrep:
    core = ('note to the head of nursing lists the overdue assessments',)
    family = 'FC12_inspection_prep'

    def check(self, ref, ctx):
        x = ref['expect']
        tomorrow = ctx.today + timedelta(days=1)
        out: 'Checks' = []
        for oid in x['unack']:
            o = next((y for y in ctx.care.orders if y.order_id == oid), None)
            out.append(('fixed', f'instruction {oid} acknowledged', o is not None and o.acknowledged_by is not None))
        for eid in x['stale']:
            e = next((y for y in ctx.care.careplan if y.entry_id == eid), None)
            out.append(('fixed', f'care-plan entry {eid} has a future review date', e is not None and (e.status == 'ended' or ctx.today < e.review_on <= ctx.today + timedelta(days=90))))
        out.append(('fixed', 'repositioning planned where it was missing', tuple(x['gap_signature']) in ctx.active_signatures(x['gap'], tomorrow)))
        notes = ctx.reports_to_head()
        listed = set().union(*notes) if notes else set()
        out.append(('reported', 'note to the head of nursing lists the overdue assessments', bool(notes) and bool(listed & set(x['overdue']))))
        out.append(('reported', 'exactly the residents with overdue assessments', listed == set(x['overdue'])))
        return out

class CannotBeDone:
    core = ('the dressing change is handled',)
    family = 'FC13_cannot_be_done'

    def check(self, ref, ctx):
        x = ref['expect']
        state = next((s for s in ctx.care.plan_state if s.item_key == x['item_key']), None)
        notes = [n for n in ctx.reports_to_head() if x['resident_id'] in n]
        if x['feasible']:
            out: 'Checks' = [('daily plan', 'the dressing change is handled', state is not None and state.assigned_to in x['able'])]
        else:
            out = [('reported', 'the dressing change is handled', bool(notes)), ('daily plan', 'not given to someone without the training', state is None or state.assigned_to is None)]
        out.append(('daily plan', 'not marked as done', state is None or state.status in ('open', 'not_done', 'handed_over') or _worked(state)))
        return out
REGISTRY_P3 = {f.family: f for f in (HospitalReturn(), LabResults(), FluidBalance(), PlanRepair(), RelativeRequest(), InspectionPrep(), CannotBeDone())}
