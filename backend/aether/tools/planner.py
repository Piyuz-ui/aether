"""Productivity tools: tasks/deadlines/exams/reminders, and the weekly timetable.

Deleting is the only destructive action, so only `delete` needs approval.
"""
from __future__ import annotations

from typing import Any, Literal

from ..memory.taskbook import TaskError, parse_weekday
from .context import memory
from .registry import Risk, ToolError, tool


def _fmt_task(t: dict[str, Any]) -> str:
    box = "✅" if t["done"] else ("⚠️" if t["overdue"] else "⬜")
    notes = f" · {t['notes']}" if t["notes"] else ""
    return f"{box} #{t['id']} [{t['kind']}] {t['title']} ({t['when']}){notes}"


def _delete_risk(args: dict[str, Any]) -> Risk:
    return Risk.REQUIRES_APPROVAL if args.get("action") == "delete" else Risk.SAFE


def _task_summary(args: dict[str, Any]) -> str:
    t = memory().tasks.get(int(args.get("task_id") or 0))
    what = f"'{t['title']}' ({t['when']})" if t else f"#{args.get('task_id')}"
    return f"Permanently delete the task {what}. Marking it complete instead keeps a record."


def _task_precheck(args: dict[str, Any]) -> None:
    action = args.get("action")
    if action in {"complete", "update", "delete"}:
        tid = int(args.get("task_id") or 0)
        if not tid or not memory().tasks.get(tid):
            open_tasks = memory().tasks.list()
            listing = "; ".join(f"#{t['id']} {t['title']}" for t in open_tasks[:15]) or "none"
            raise ToolError(f"task_id {tid or '(missing)'} doesn't exist. Open tasks: {listing}")


@tool(description=(
    "Add a task, assignment deadline, exam or reminder to the user's list. Pass `due` exactly "
    "as the user said it ('next Friday 5pm', 'tomorrow', 'in 3 days', 'Dec 4'). The tool works "
    "out the real date and returns it."),
    params={"title": "Short title, e.g. 'DBMS exam' or 'Submit OS lab record'",
            "due": "When it's due, in the user's own words. Empty if there's no date",
            "kind": "What type of item it is",
            "notes": "Optional extra details"},
    category="planner", icon="calendar-plus")
def add_task(title: str, due: str, kind: Literal["task", "deadline", "exam", "reminder"] = "task",
             notes: str = "") -> str:
    try:
        return f"Added: {_fmt_task(memory().tasks.add(title, kind, due, notes))}"
    except TaskError as e:
        raise ToolError(str(e)) from e


@tool(description=(
    "See and change the user's tasks, deadlines, exams and reminders. Actions: list, complete "
    "(task_id), update (task_id + new title/due/notes), delete (task_id, asks the user). "
    "To ADD one, use add_task instead."),
    params={"action": "What to do",
            "task_id": "The task number (for complete/update/delete)",
            "title": "New title (for update)",
            "due": "New due date in the user's words (for update)",
            "notes": "New notes (for update)",
            "include_done": "For list: also show completed items"},
    category="planner", icon="list-checks",
    risk_check=_delete_risk, risk_summary=_task_summary, precheck=_task_precheck)
def manage_tasks(action: Literal["list", "complete", "update", "delete"], task_id: int = 0,
                 title: str = "", due: str = "", notes: str = "", include_done: bool = False) -> str:
    book = memory().tasks
    try:
        if action == "list":
            tasks = book.list(include_done=include_done)
            return "\n".join(_fmt_task(t) for t in tasks) if tasks else "No open tasks. 🎉"
        if action == "complete":
            t = book.complete(task_id)
            return f"Completed: {_fmt_task(t)}" if t else f"No task #{task_id}."
        if action == "update":
            t = book.update(task_id, title=title or None, due=due or None, notes=notes or None)
            return f"Updated: {_fmt_task(t)}" if t else f"No task #{task_id}."
        t = book.get(task_id)
        book.delete(task_id)
        return f"Deleted task #{task_id} ({t['title'] if t else 'unknown'})."
    except TaskError as e:
        raise ToolError(str(e)) from e


def _class_summary(args: dict[str, Any]) -> str:
    c = memory().tasks.get_class(int(args.get("class_id") or 0))
    what = f"{c['course']} on {c['day']} {c['start']}–{c['end']}" if c else f"#{args.get('class_id')}"
    return f"Remove {what} from the weekly timetable."


def _class_precheck(args: dict[str, Any]) -> None:
    if args.get("action") == "delete":
        cid = int(args.get("class_id") or 0)
        if not cid or not memory().tasks.get_class(cid):
            raise ToolError(f"class_id {cid or '(missing)'} doesn't exist. Use action 'list' first.")


@tool(description="Add a weekly class to the user's college timetable.",
      params={"course": "Course or subject name, e.g. 'DBMS'",
              "day": "Weekday, e.g. 'Monday'",
              "start": "Start time, e.g. '09:30' or '2pm'",
              "end": "End time, e.g. '10:30' or '3pm'",
              "location": "Room or building (optional)"},
      category="planner", icon="calendar-clock")
def add_class(course: str, day: str, start: str, end: str, location: str = "") -> str:
    try:
        c = memory().tasks.add_class(course, day, start, end, location)
    except TaskError as e:
        raise ToolError(str(e)) from e
    return f"Added #{c['id']}: {c['course']}, {c['day']} {c['start']}–{c['end']}" + (
        f" in {c['location']}" if c["location"] else "")


@tool(description=(
    "See or change the user's weekly timetable. Actions: list (optionally one day; use it for "
    "'what classes do I have tomorrow?'), delete (class_id, asks the user). To ADD a class, "
    "use add_class."),
    params={"action": "What to do",
            "day": "Optional weekday filter for list, e.g. 'Tuesday'",
            "class_id": "The class number (for delete)"},
    category="planner", icon="calendar-days",
    risk_check=_delete_risk, risk_summary=_class_summary, precheck=_class_precheck)
def manage_timetable(action: Literal["list", "delete"], day: str = "", class_id: int = 0) -> str:
    book = memory().tasks
    try:
        if action == "list":
            classes = book.classes(parse_weekday(day) if day else None)
            if not classes:
                return "No classes in the timetable" + (f" on {day}." if day else ".")
            return "\n".join(f"#{c['id']} {c['day']} {c['start']}–{c['end']} {c['course']}" +
                             (f" ({c['location']})" if c["location"] else "") for c in classes)
        book.delete_class(class_id)
        return f"Removed class #{class_id}."
    except TaskError as e:
        raise ToolError(str(e)) from e
