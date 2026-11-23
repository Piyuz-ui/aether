"""Tasks, deadlines, exams, reminders, and the weekly college timetable."""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from typing import Any

from .store import Store

TASK_KINDS = ("task", "deadline", "exam", "reminder")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class TaskError(ValueError):
    pass


_TIME_RE = re.compile(r"\b(?:at\s+)?(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2})\b")
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "ten": 10, "fourteen": 14}
_DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d %b", "%d %B", "%b %d", "%B %d")


def parse_due(value: str, now: datetime | None = None) -> str | None:
    """Turn what the user said into an ISO date ('2026-12-04' or '2026-12-04T17:00').

    Small models are unreliable at calendar maths, so this is done in code. Accepts ISO dates,
    'today', 'tomorrow', 'day after tomorrow', 'in 3 days', 'in 2 weeks', 'friday', 'this friday',
    'next friday' (= Friday of next week), 'Dec 4', '4 December 2026', plus an optional time
    such as 'at 5pm' or '17:30'.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("/", "-"))
        has_time = "T" in raw or ":" in raw
        return dt.strftime("%Y-%m-%dT%H:%M") if has_time else dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    now = now or datetime.now()
    today = now.date()
    v = raw.lower().replace(",", " ")
    clock = None
    m = _TIME_RE.search(v)
    if m:
        clock = parse_clock(m.group(1).replace(" ", ""))
        v = v[:m.start()] + v[m.end():]
    v = re.sub(r"\b(on|by|due|the|before|until)\b", " ", v)
    v = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", v)
    v = " ".join(v.split())

    day: date | None = None
    if v in {"", "today", "tonight"}:
        day = today
    elif v == "tomorrow":
        day = today + timedelta(days=1)
    elif v in {"day after tomorrow", "overmorrow"}:
        day = today + timedelta(days=2)
    elif m2 := re.fullmatch(r"in (\d+|[a-z]+) (day|days|week|weeks)", v):
        n = int(m2.group(1)) if m2.group(1).isdigit() else _NUMBERS.get(m2.group(1), 0)
        if n:
            day = today + timedelta(days=n * (7 if m2.group(2).startswith("week") else 1))
    elif m3 := re.fullmatch(r"(this |coming |next )?([a-z]+)", v):
        try:
            target = parse_weekday(m3.group(2))
        except TaskError:
            target = -1
        if target >= 0:
            if (m3.group(1) or "").strip() == "next":
                next_monday = today + timedelta(days=7 - today.weekday())
                day = next_monday + timedelta(days=target)
            else:
                ahead = (target - today.weekday()) % 7 or 7
                day = today + timedelta(days=ahead)
    if day is None:
        for fmt in _DATE_FORMATS:
            has_year = "%Y" in fmt
            # Always parse with an explicit year: year-less strptime is deprecated (and
            # ambiguous for 29 Feb). A date already past this year means next year.
            text, full_fmt = (v, fmt) if has_year else (f"{v} {today.year}", f"{fmt} %Y")
            try:
                day = datetime.strptime(text.title(), full_fmt).date()
            except ValueError:
                continue
            if not has_year and day < today:
                try:
                    day = day.replace(year=today.year + 1)
                except ValueError:  # 29 Feb → next year has none
                    day = None
                    continue
            break
    if day is None:
        raise TaskError(f"I couldn't understand the date '{raw}'. Use e.g. 2026-12-04, "
                        "'tomorrow 5pm', 'next Friday' or 'Dec 4'.")
    return f"{day.isoformat()}T{clock}" if clock else day.isoformat()


def parse_weekday(value: str | int) -> int:
    if isinstance(value, int) or str(value).strip().isdigit():
        n = int(value)
        if 0 <= n <= 6:
            return n
        raise TaskError("Weekday numbers go from 0 (Monday) to 6 (Sunday).")
    v = str(value).strip().lower()
    for i, name in enumerate(WEEKDAYS):
        if v and name.startswith(v[:3]):
            return i
    raise TaskError(f"'{value}' isn't a weekday.")


def parse_clock(value: str) -> str:
    v = (value or "").strip().lower().replace(".", ":")
    for fmt in ("%H:%M", "%I:%M%p", "%I:%M %p", "%I%p", "%I %p", "%H"):
        try:
            return datetime.strptime(v, fmt).strftime("%H:%M")
        except ValueError:
            continue
    raise TaskError(f"Time '{value}' should look like 09:30 or 2:15pm.")


def _due_dt(due: str | None) -> datetime | None:
    if not due:
        return None
    dt = datetime.fromisoformat(due)
    return dt if "T" in due else dt.replace(hour=23, minute=59)


def describe_due(due: str | None, now: datetime | None = None) -> str:
    """'due tomorrow 17:00', 'overdue by 2 days', 'due Fri 04 Dec'."""
    dt = _due_dt(due)
    if dt is None:
        return "no due date"
    now = now or datetime.now()
    days = (dt.date() - now.date()).days
    clock = f" {dt:%H:%M}" if due and "T" in due else ""
    if dt < now:
        late = (now.date() - dt.date()).days
        return f"overdue by {late} day{'s' * (late != 1)}" if late else f"overdue (was due today{clock})"
    if days == 0:
        return f"due today{clock}"
    if days == 1:
        return f"due tomorrow{clock}"
    if days < 7:
        return f"due {dt:%A}{clock} (in {days} days)"
    return f"due {dt:%a %d %b}{clock} (in {days} days)"


class TaskBook:
    def __init__(self, store: Store):
        self.store = store

    # ----------------------------------------------------------------- tasks
    def add(self, title: str, kind: str = "task", due: str = "", notes: str = "") -> dict[str, Any]:
        title = title.strip()
        if not title:
            raise TaskError("A task needs a title.")
        if kind not in TASK_KINDS:
            raise TaskError(f"kind must be one of {', '.join(TASK_KINDS)}.")
        now = time.time()
        cur = self.store.execute(
            "INSERT INTO tasks (title, kind, due, notes, created, updated) VALUES (?, ?, ?, ?, ?, ?)",
            (title, kind, parse_due(due), notes.strip(), now, now))
        return self.get(cur.lastrowid) or {}

    def get(self, task_id: int) -> dict[str, Any] | None:
        row = self.store.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return self._decorate(row) if row else None

    def list(self, include_done: bool = False, within_days: int | None = None) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM tasks" + ("" if include_done else " WHERE done = 0") +
                                " ORDER BY due IS NULL, due, id")
        out = [self._decorate(r) for r in rows]
        if within_days is not None:
            limit = datetime.now() + timedelta(days=within_days)
            out = [t for t in out if t["due"] and _due_dt(t["due"]) <= limit]  # type: ignore[operator]
        return out

    def update(self, task_id: int, **fields: Any) -> dict[str, Any] | None:
        if not self.get(task_id):
            return None
        allowed: dict[str, Any] = {}
        if fields.get("title"):
            allowed["title"] = str(fields["title"]).strip()
        if fields.get("kind"):
            if fields["kind"] not in TASK_KINDS:
                raise TaskError(f"kind must be one of {', '.join(TASK_KINDS)}.")
            allowed["kind"] = fields["kind"]
        if "due" in fields and fields["due"] is not None:
            allowed["due"] = parse_due(fields["due"])
        if fields.get("notes") is not None and "notes" in fields:
            allowed["notes"] = str(fields["notes"])
        if "done" in fields and fields["done"] is not None:
            allowed["done"] = int(bool(fields["done"]))
        if allowed:
            allowed["updated"] = time.time()
            sets = ", ".join(f"{k} = ?" for k in allowed)
            self.store.execute(f"UPDATE tasks SET {sets} WHERE id = ?", (*allowed.values(), task_id))
        return self.get(task_id)

    def complete(self, task_id: int, done: bool = True) -> dict[str, Any] | None:
        return self.update(task_id, done=done)

    def delete(self, task_id: int) -> bool:
        return self.store.execute("DELETE FROM tasks WHERE id = ?", (task_id,)).rowcount > 0

    @staticmethod
    def _decorate(row: dict[str, Any]) -> dict[str, Any]:
        row["done"] = bool(row["done"])
        row["when"] = "done" if row["done"] else describe_due(row["due"])
        dt = _due_dt(row["due"])
        row["overdue"] = bool(dt and not row["done"] and dt < datetime.now())
        return row

    # -------------------------------------------------------------- timetable
    def add_class(self, course: str, day: str | int, start: str, end: str,
                  location: str = "", notes: str = "") -> dict[str, Any]:
        if not course.strip():
            raise TaskError("A class needs a course name.")
        wd, s, e = parse_weekday(day), parse_clock(start), parse_clock(end)
        if e <= s:
            raise TaskError("The class must end after it starts.")
        cur = self.store.execute(
            "INSERT INTO timetable (course, weekday, start, end, location, notes, created) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (course.strip(), wd, s, e, location.strip(),
                                             notes.strip(), time.time()))
        return self.get_class(cur.lastrowid) or {}

    def get_class(self, class_id: int) -> dict[str, Any] | None:
        row = self.store.one("SELECT * FROM timetable WHERE id = ?", (class_id,))
        return self._decorate_class(row) if row else None

    def classes(self, weekday: int | None = None) -> list[dict[str, Any]]:
        if weekday is None:
            rows = self.store.query("SELECT * FROM timetable ORDER BY weekday, start")
        else:
            rows = self.store.query("SELECT * FROM timetable WHERE weekday = ? ORDER BY start",
                                    (weekday,))
        return [self._decorate_class(r) for r in rows]

    def classes_on(self, day: date) -> list[dict[str, Any]]:
        return self.classes(day.weekday())

    def delete_class(self, class_id: int) -> bool:
        return self.store.execute("DELETE FROM timetable WHERE id = ?", (class_id,)).rowcount > 0

    @staticmethod
    def _decorate_class(row: dict[str, Any]) -> dict[str, Any]:
        row["day"] = WEEKDAYS[row["weekday"]].capitalize()
        return row
