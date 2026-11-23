"""Interactive study: quizzes rendered as glass cards in the web app."""
from __future__ import annotations

from typing import Any

from ..memory.quizzes import QuizError
from .context import memory
from .registry import ToolError, tool

QUESTION_SCHEMA: dict[str, Any] = {
    "type": "array",
    "description": "4–8 questions",
    "items": {
        "type": "object",
        "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"},
                        "description": "4 choices for multiple choice. Omit for a flashcard"},
            "answer": {"type": "string", "description": "The correct option, copied exactly"},
            "explanation": {"type": "string", "description": "One sentence on why"},
            "topic": {"type": "string", "description": "Short topic name, e.g. 'Normalization'"},
        },
        "required": ["question", "answer", "topic"],
    },
}


@tool(description=(
    "Create an interactive quiz the user answers in the app (multiple-choice cards, or "
    "flip-cards when you omit options). Use it for 'quiz me', 'test me', 'study my notes'. Base "
    "questions on search_knowledge_base passages when the user has notes. After it succeeds, "
    "reply in ONE short line. The app shows the cards, so don't repeat the questions."),
    params={"title": "Quiz title, e.g. 'DBMS: normalization'",
            "questions": "The questions"},
    schemas={"questions": QUESTION_SCHEMA},
    category="study", icon="graduation-cap")
def make_quiz(title: str, questions: list) -> str:  # type: ignore[type-arg]
    try:
        quiz = memory().quizzes.create(title, questions)
    except QuizError as e:
        raise ToolError(str(e)) from e
    n = len(quiz["questions"])
    topics = sorted({q["topic"] for q in quiz["questions"]})
    return (f"Quiz #{quiz['id']} ready: '{quiz['title']}', {n} question{'s' * (n != 1)} "
            f"({', '.join(topics)}). The app is showing it as interactive cards.")
