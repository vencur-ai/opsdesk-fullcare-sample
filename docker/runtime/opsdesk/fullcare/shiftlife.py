"""Life on the unit during a long shift (long tier): arrivals during the shift and colleagues working their own plan.

Both depend only on the workspace clock, which moves with the number of changes (and `shift.wait`), so a run is
deterministic: the same calls give the same arrivals and the same ticked-off items.

- `fire_arrivals`: a `StagedArrival` (mails, chat posts) appears at the first change at or after its clock time; the
  messages carry that time as their sending time.
- `colleagues_tick`: when the clock passes an item's due time plus the house's plan window, the colleague it is
  assigned to ticks it off – but only if they are really at work (on the rota, not absent, not in `care.away`) and
  may do it (qualification, skills). Items left with someone absent or unqualified stay open.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from opsdesk.apps.ids import next_monotonic_id
from opsdesk.fullcare import dailyplan

DEFAULT_START = 8 * 60 + 5  # Workforce's clock start


def sync_clock(ws) -> None:
    """The app clock starts where the care state says (long tier) plus any waiting; otherwise the Workforce default."""
    if ws.app is None or ws.care is None:
        return
    start = ws.care.clock_start
    ws.app._clock_base = DEFAULT_START if start is None else start + ws.care.waited_min


def _now(ws) -> datetime:
    return datetime.strptime(ws.app.now(ws.state.current_date), "%Y-%m-%d %H:%M")


def fire_arrivals(ws) -> bool:
    care, app = ws.care, ws.app
    now = _now(ws)
    fired = False
    for staged in care.staged:
        if staged.fired_at is not None:
            continue
        at = now.replace(hour=int(staged.at[:2]), minute=int(staged.at[3:]))
        if now < at:
            continue
        stamp = now.strftime("%Y-%m-%d %H:%M")
        for mail in staged.mails:  # new ids: the nurse may have sent mails since the task was made
            app.mails.append(mail.model_copy(update={"sent_at": stamp, "mail_id": next_monotonic_id("mail", [m.mail_id for m in app.mails])}))
        for chat in staged.chats:
            app.chat_messages.append(chat.model_copy(update={
                "sent_at": stamp, "chat_id": next_monotonic_id("chat", [m.chat_id for m in app.chat_messages])}))
        staged.fired_at = stamp
        fired = True
    return fired


def colleagues_tick(ws) -> bool:
    care, world = ws.care, ws.state
    if not care.colleagues_work:
        return False
    now = _now(ws)
    window = timedelta(minutes=care.standard.plan_window_minutes)
    today = world.current_date
    ticked = False
    for shift in ("early", "late"):
        staff = {s.employee_id: s for s in dailyplan.on_shift(world, care, today, shift) if s.employee_id not in care.away}
        for item in dailyplan.items_for(care, today, shift):
            if item.status != "open" or item.assigned_to is None or item.due + window > now:
                continue
            who = staff.get(item.assigned_to)
            if who is None or dailyplan.eligibility(item, who):
                continue
            dailyplan.set_state(care, item.item_key, at=(item.due + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M"),
                                status="done", by=who.employee_id)
            ticked = True
    return ticked


def after_change(ws) -> None:
    if ws.care is None or ws.care.clock_start is None:
        return
    fire_arrivals(ws)
    colleagues_tick(ws)
