"""Checks of the OpsDesk FullCare task families (buyer and verifier image).

The scenario builders and reference solutions are not part of this build.
"""

from __future__ import annotations
import re
from opsdesk.fullcare import dailyplan
from opsdesk.fullcare.families import REGISTRY
START = 6 * 60 + 5
EARLY_OFFSET = -150
OFFSET = {'FC3_vital_escalation': -125}
AT_START = ('FC3_vital_escalation', 'FC13_cannot_be_done', 'FC5_doctor_orders', 'FC8_lab_results', 'FC9_fluid_balance', 'FC2_assessment_prevention')
LATER = (('FC6_hospital_transfer', ('07:40', '07:50', '08:00')), ('FC7_hospital_return', ('08:30', '08:45', '09:00')), ('FC11_relative_request', ('09:20', '09:35', '09:50')), ('FC1_admission', ('10:00', '10:15', '10:30')), ('FC3_vital_escalation', ('11:30', '11:40', '11:50')), ('FC9_fluid_balance', ('12:00', '12:10', '12:20')))
VARIANT = {'FC3_vital_escalation': (0, 1), 'FC6_hospital_transfer': (0,), 'FC2_assessment_prevention': (0, 2, 3, 5), 'FC8_lab_results': (0, 2, 3, 5)}
MIN_CALLS = 100
LABEL = {'FC1_admission': 'admission', 'FC6_hospital_transfer': 'emergency transfer', 'FC2_assessment_prevention': 'assessment', 'FC3_vital_escalation': 'values', 'FC5_doctor_orders': 'GP instructions', 'FC7_hospital_return': 'return from hospital', 'FC8_lab_results': 'lab report', 'FC9_fluid_balance': 'fluids', 'FC10_plan_repair': 'sick call', 'FC11_relative_request': 'relative', 'FC13_cannot_be_done': 'dressing change'}
KEYWORDS = {'FC3_vital_escalation': ['blutdruck', 'rr', 'blutzucker', 'bz', 'temperatur', 'fieber', 'sättigung', 'spo2', 'sauerstoff', 'blood pressure', 'bp', 'blood sugar', 'glucose', 'temperature', 'temp', 'saturation', 'oxygen', 'nachmess', 'repeat', 'wert', 'value'], 'FC7_hospital_return': ['krankenhaus', 'klinik', 'zurück', 'hospital', 'back'], 'FC8_lab_results': ['labor', 'blut', 'lab', 'blood'], 'FC13_cannot_be_done': ['verband', 'wunde', 'dressing', 'wound'], 'FC1_admission': ['einzug', 'eingezogen', 'neu', 'aufnahme', 'admission', 'admitted', 'moved in', 'new'], 'FC6_hospital_transfer': ['krankenhaus', 'klinik', 'sturz', 'gestürzt', 'notfall', 'hospital', 'fall', 'fell', 'emergency']}
FAMILY = 'FC14_early_shift'
ABBREV = ('mr.', 'mrs.', 'ms.', 'dr.', 'hr.', 'fr.', 'nr.', 'ca.', 'bzw.', 'z.b.', 'u.a.', 'std.', 'st.')
BLOCK_RESIDENTS = 3
BLOCK_FAMILIES = 3
OUTSIDE = 3

def _segments(text):
    """Lines, and sentences within a line."""
    out = []
    for line in text.splitlines():
        cur = ''
        for piece in re.split('(?<=[.!?;])\\s+', line.strip()):
            cur = f'{cur} {piece}' if cur else piece
            if not cur.casefold().endswith(ABBREV):
                out.append(cur)
                cur = ''
        if cur:
            out.append(cur)
    return [s for s in out if s.strip()]

def _blocks(ctx, text):
    """The handover as points: a line or sentence that names residents starts a point, one that names nobody belongs to
    the point before it ("Frau Koch:" followed by "- RR 180/100" is one point)."""
    out: 'list[tuple[set[str], str]]' = []
    for s in _segments(text):
        who = ctx.named(s)
        if who or not out:
            out.append((who, s))
        else:
            out[-1] = (out[-1][0], f'{out[-1][1]} {s}')
    return out

def _said(text, family):
    """A word for this kind of open matter: short words (rr, bp, neu, fall …) must start a word, longer ones may sit
    inside a compound (Blutdruckwert, Wundverband)."""
    low = text.casefold()
    return any((re.search(('(?<!\\w)' if len(k) <= 4 else '') + re.escape(k), low) for k in KEYWORDS[family]))

class EarlyShift:
    family = FAMILY
    core = ('handover written for the late shift',)

    def core_for(self, ref):
        return self.core + tuple((f"{p['label']}: {name}" for p in ref['expect']['parts'] for name in REGISTRY[p['family']].core))

    @staticmethod
    def part_ref(ref, part):
        return {**ref, 'family': part['family'], 'expect': part['expect'], 'scope': part['scope'], 'sources': part['sources'], 'assessment_sources': part['assessment_sources'], 'eligible': part['eligible'], 'now': part['now']}

    def check(self, ref, ctx):
        x = ref['expect']
        out: 'Checks' = []
        for p in x['parts']:
            for _, name, ok in REGISTRY[p['family']].check(self.part_ref(ref, p), ctx):
                out.append((f"part · {p['label']}", f"{p['label']}: {name}", ok))
        old = {h.handover_id for h in ctx.initial.handovers}
        new = [h for h in ctx.care.handovers if h.handover_id not in old and h.ward_id == x['ward_id'] and (h.shift == 'early')]
        out.append(('handover', 'handover written for the late shift', bool(new)))
        points = [(who, {f for f in KEYWORDS if _said(text, f)}) for h in new for who, text in _blocks(ctx, h.text)]
        for m in x['open_matters']:
            rid = _named_id(ctx, m)
            out.append(('handover', f"handover names {' '.join(m['name'])} with what is open", any((rid in who and len(who) <= BLOCK_RESIDENTS and (m['family'] in fams) and (len(fams) <= BLOCK_FAMILIES) for who, fams in points))))
        dealt = set(ref['scope']) | {_named_id(ctx, m) for m in x['open_matters']}
        outside = {r for who, _ in points for r in who} - dealt
        out.append(('handover', f'handover names at most {OUTSIDE} residents the shift did not deal with', len(outside) <= OUTSIDE))
        away = set(ctx.care.away)
        staff = {s.employee_id for s in dailyplan.on_shift(ctx.world, ctx.care, ctx.today, 'early')} - away
        items = dailyplan.items_for(ctx.care, ctx.today, 'early')
        out.append(('shift', 'no open item left with someone who is not at work', all((i.assigned_to in staff for i in items if i.status == 'open' and i.assigned_to is not None))))
        before = {s.item_key: (s.status, s.by) for s in ctx.initial.plan_state}
        out.append(('shift', 'nothing ticked off for a colleague', all((s.by for s in ctx.care.plan_state if s.status == 'done' and before.get(s.item_key) != (s.status, s.by)))))
        return out

def _named_id(ctx, m):
    """The resident an open matter is about; the new resident gets their id only at admission (found by name)."""
    if ctx.care.resident(m['resident_id']) is not None:
        return m['resident_id']
    return next((r.resident_id for r in ctx.care.residents if ctx.initial.resident(r.resident_id) is None and [r.given_name, r.family_name] == m['name']), m['resident_id'])
