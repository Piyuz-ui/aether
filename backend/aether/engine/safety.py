"""Human-in-the-loop safety gate.

When the agent wants to run a REQUIRES_APPROVAL tool, it opens an approval request and
awaits it. That suspends the loop on an asyncio.Future with no polling. Whoever is driving
(the CLI prompt, or POST /approvals/{id} in the API) calls resolve(), and the loop continues.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Decision:
    approved: bool
    reason: str = ""


@dataclass
class ApprovalRequest:
    id: str
    run_id: str
    tool: str
    args: dict[str, Any]
    summary: str
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"approval_id": self.id, "run_id": self.run_id, "name": self.tool,
                "args": self.args, "summary": self.summary, "created": self.created}


class ApprovalBroker:
    def __init__(self, timeout_s: float = 600.0):
        self.timeout_s = timeout_s
        self._pending: dict[str, tuple[ApprovalRequest, asyncio.Future[Decision]]] = {}

    def open(self, *, run_id: str, tool: str, args: dict[str, Any], summary: str) -> ApprovalRequest:
        req = ApprovalRequest(id=uuid.uuid4().hex[:12], run_id=run_id, tool=tool, args=args,
                              summary=summary)
        fut: asyncio.Future[Decision] = asyncio.get_running_loop().create_future()
        self._pending[req.id] = (req, fut)
        return req

    def resolve(self, approval_id: str, approved: bool, reason: str = "") -> bool:
        """Answer a pending request. Returns False if it's unknown or already answered."""
        entry = self._pending.get(approval_id)
        if entry is None or entry[1].done():
            return False
        entry[1].set_result(Decision(approved=approved, reason=reason))
        return True

    async def wait(self, approval_id: str) -> Decision:
        entry = self._pending.get(approval_id)
        if entry is None:
            return Decision(False, "Unknown approval request.")
        try:
            return await asyncio.wait_for(asyncio.shield(entry[1]), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            return Decision(False, f"No answer within {self.timeout_s:.0f} s, so it was denied.")
        finally:
            self._pending.pop(approval_id, None)

    def pending(self, run_id: str | None = None) -> list[ApprovalRequest]:
        return [r for r, f in self._pending.values()
                if not f.done() and (run_id is None or r.run_id == run_id)]

    def cancel_run(self, run_id: str) -> None:
        """Deny everything still open for a run (e.g. the client disconnected)."""
        for req, fut in list(self._pending.values()):
            if req.run_id == run_id and not fut.done():
                fut.set_result(Decision(False, "The run was cancelled."))
