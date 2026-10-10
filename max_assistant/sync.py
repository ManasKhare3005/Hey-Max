"""Phone <-> laptop sync for reminders and events.

The phone keeps its own copy of both (so reminders fire and events show in its "Max" calendar
with the laptop off) and saves changes locally first. Whenever it reaches the laptop it posts
what changed since the last sync; the laptop merges those into its database and answers with
its current list, which the phone takes as the truth for everything it hasn't changed since.

Items are matched by `uid`. When both sides changed the same item:
  - finished beats open: done / cancelled always beats pending (a reminder the phone already
    showed must not come back because the laptop edited its text a minute earlier);
  - otherwise the later edit (`updated_ms`) wins; a tie keeps what's there.
"""
from __future__ import annotations

FINISHED = {"done", "cancelled"}


def wins(incoming: dict, current: dict) -> bool:
    """Should `incoming` replace `current`? (Both have `status` and `updated_ms`.)"""
    a, b = incoming.get("status") in FINISHED, current.get("status") in FINISHED
    if a != b:
        return a
    return int(incoming.get("updated_ms") or 0) > int(current.get("updated_ms") or 0)


def sync(ctx, body: dict) -> dict:
    """POST /api/sync: apply the phone's changes, return the laptop's reminders and events."""
    out: dict = {"reminders": [], "events": []}
    reminders = getattr(ctx, "reminders", None)
    if reminders is not None:
        for item in body.get("reminders") or []:
            reminders.apply_sync(item)
        current = reminders.for_sync()
        reminders.mark_on_phone([r.uid for r in current])       # the phone holds these from now on
        out["reminders"] = [r.to_sync() for r in current]
    events = getattr(ctx, "events", None)
    if events is not None:
        for item in body.get("events") or []:
            events.apply_sync(item)
        out["events"] = [e.to_sync() for e in events.for_sync()]
    return out
