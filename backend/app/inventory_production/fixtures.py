"""Mock replies for the schedule proposal (BAKE-20) and the shortage explanation
(BAKE-31): tests and offline demos only.

``GreedyMockPlanner`` stands in for the language model in Mock mode. It reads the
TaskRequest from the prompt and lays the orders out earliest-due first from
06:00 on the plan's first day, a bake task (two thirds of the work) then a
finish task, skipping busy intervals. It never checks its own plan: the
same ``ProductionService.validate`` as for a real model decides whether it is
usable. It is labelled Mock and is not a planning algorithm for production.

``MockShortageExplainer`` writes one sentence per short ingredient from the
TaskRequest's own quantities; ``check_explanation`` still checks it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FIRST_START = time(6, 0)
MOCK_CONFIDENCE = 0.8


def proposal_reply(tasks: list[dict], *, confidence: float = MOCK_CONFIDENCE, rationale: str = "Mock plan.") -> str:
    """A ScheduleProposal reply; each task is {order_id, task_kind, start_at, end_at}."""
    return json.dumps({"tasks": tasks, "rationale": rationale, "confidence": confidence})


@dataclass
class GreedyMockPlanner:
    """Implements ILLMClient for the ``propose_schedule`` task only."""

    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        request = json.loads(prompt.rsplit("TaskRequest: ", 1)[1])
        data = request["inputData"]
        zone = ZoneInfo(data["plan"]["time_zone"])
        busy = sorted(
            (datetime.fromisoformat(b["start_at"]), datetime.fromisoformat(b["end_at"])) for b in data["busy"]
        )
        cursor = datetime.combine(date.fromisoformat(data["plan"]["start_date"]), FIRST_START, zone)
        tasks = []
        for order in sorted(data["orders"], key=lambda o: o["required_at"]):
            bake = max(1, -(-order["work_minutes"] * 2 // 3))
            finish = max(1, order["work_minutes"] - bake)
            for kind, minutes in (("bake", bake), ("finish", finish)):
                cursor = _after_busy(cursor, timedelta(minutes=minutes), busy)
                end = cursor + timedelta(minutes=minutes)
                tasks.append(
                    {
                        "order_id": order["order_id"],
                        "task_kind": kind,
                        "start_at": cursor.isoformat(),
                        "end_at": end.isoformat(),
                    }
                )
                cursor = end
        return proposal_reply(tasks, rationale="Mock plan: orders in due order from 06:00, bake then finish.")


def explanation_reply(text: str) -> str:
    """A ShortageExplanation reply."""
    return json.dumps({"text": text})


@dataclass
class MockShortageExplainer:
    """Implements ILLMClient for the ``explain_shortages`` task only."""

    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        lines = json.loads(prompt.rsplit("TaskRequest: ", 1)[1])["inputData"]["ingredients"]
        sentences = [
            f"{line['ingredient']}: short by {line['short_by']} {line['unit']}"
            f" (the order needs {line['required']} {line['unit']})."
            for line in lines
            if line["short_by"] is not None
        ]
        if any(line["short_by"] is None for line in lines):
            sentences.append("Everything else is covered by stock.")
        return explanation_reply(" ".join(sentences))


def _after_busy(start: datetime, length: timedelta, busy: list[tuple[datetime, datetime]]) -> datetime:
    """The first start at or after ``start`` that does not overlap a busy interval."""
    for busy_start, busy_end in busy:
        if start < busy_end and start + length > busy_start:
            start = busy_end
    return start
