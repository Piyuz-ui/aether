"""Quizzes and weak-topic tracking.

The agent writes a quiz with the make_quiz tool; the UI renders it as interactive cards and
reports each answer. Per-topic accuracy is kept in SQLite, and a topic the user keeps missing
becomes a long-term memory fact, so future study help can focus on it.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .facts import FactMemory
from .store import Store

MAX_QUESTIONS = 12
WEAK_MIN_ATTEMPTS = 2
WEAK_ACCURACY = 0.6


class QuizError(ValueError):
    pass


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """Clean up what a small model wrote: resolve 'B' / index answers, drop broken items."""
    if isinstance(raw, dict):
        raw = raw.get("questions", [raw])
    if not isinstance(raw, list):
        raise QuizError("`questions` must be a list.")
    out: list[dict[str, Any]] = []
    for item in raw[:MAX_QUESTIONS]:
        if not isinstance(item, dict):
            continue
        q = _norm(item.get("question") or item.get("q") or item.get("prompt"))
        answer = item.get("answer", item.get("correct", item.get("correct_answer", "")))
        options = [_norm(o) for o in (item.get("options") or item.get("choices") or []) if _norm(o)]
        options = list(dict.fromkeys(options))[:6]
        if not q:
            continue
        if options:
            idx = None
            if isinstance(answer, int) and 0 <= answer < len(options):
                idx = answer
            else:
                a = _norm(answer)
                letter = re.fullmatch(r"\(?([A-Fa-f])\)?[.)]?", a)
                if letter and ord(letter.group(1).upper()) - 65 < len(options):
                    idx = ord(letter.group(1).upper()) - 65
                else:
                    for i, o in enumerate(options):
                        if o.lower() == a.lower() or re.sub(r"^[A-Fa-f][.)]\s*", "", o).lower() == a.lower():
                            idx = i
                            break
            if idx is None or len(options) < 2:
                continue  # the answer isn't one of the options: unusable question
            out.append({"type": "mcq", "question": q, "options": options, "answer": idx,
                        "explanation": _norm(item.get("explanation") or item.get("why")),
                        "topic": _norm(item.get("topic")) or "general"})
        elif _norm(answer):
            out.append({"type": "flashcard", "question": q, "answer": _norm(answer),
                        "explanation": _norm(item.get("explanation")),
                        "topic": _norm(item.get("topic")) or "general"})
    if not out:
        raise QuizError("None of the questions were usable. Each needs a `question` and an `answer`; "
                        "for multiple choice, `answer` must be one of the `options`.")
    return out


class QuizBook:
    def __init__(self, store: Store, facts: FactMemory):
        self.store = store
        self.facts = facts

    def create(self, title: str, questions: Any) -> dict[str, Any]:
        items = normalize_questions(questions)
        cur = self.store.execute("INSERT INTO quizzes (title, data, created) VALUES (?, ?, ?)",
                                 (_norm(title) or "Quiz", json.dumps(items), time.time()))
        return self.get(cur.lastrowid) or {}

    def get(self, quiz_id: int) -> dict[str, Any] | None:
        row = self.store.one("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
        if not row:
            return None
        return {"id": row["id"], "title": row["title"], "questions": json.loads(row["data"]),
                "created": row["created"]}

    async def record(self, quiz_id: int, index: int, correct: bool) -> dict[str, Any]:
        quiz = self.get(quiz_id)
        if not quiz or not 0 <= index < len(quiz["questions"]):
            raise QuizError("No such question.")
        topic = quiz["questions"][index]["topic"]
        col = "correct" if correct else "wrong"
        self.store.execute(
            f"INSERT INTO quiz_stats (topic, {col}, updated) VALUES (?, 1, ?) "
            f"ON CONFLICT(topic) DO UPDATE SET {col} = {col} + 1, updated = excluded.updated",
            (topic, time.time()))
        stat = self.store.one("SELECT * FROM quiz_stats WHERE topic = ?", (topic,)) or {}
        weak = self._is_weak(stat)
        if weak and not correct:
            acc = round(100 * stat["correct"] / (stat["correct"] + stat["wrong"]))
            await self.facts.add(f"The user finds {topic} difficult (quiz accuracy {acc}%).",
                                 "academic", source="auto")
        return {"topic": topic, "correct": stat.get("correct", 0), "wrong": stat.get("wrong", 0),
                "weak": weak}

    @staticmethod
    def _is_weak(stat: dict[str, Any]) -> bool:
        total = stat.get("correct", 0) + stat.get("wrong", 0)
        return total >= WEAK_MIN_ATTEMPTS and stat.get("correct", 0) / total < WEAK_ACCURACY

    def stats(self) -> list[dict[str, Any]]:
        rows = self.store.query("SELECT * FROM quiz_stats ORDER BY updated DESC")
        for r in rows:
            total = r["correct"] + r["wrong"]
            r["accuracy"] = round(r["correct"] / total, 3) if total else 0.0
            r["weak"] = self._is_weak(r)
        return sorted(rows, key=lambda r: (not r["weak"], r["accuracy"]))

    def weak_topics(self) -> list[dict[str, Any]]:
        return [r for r in self.stats() if r["weak"]]
