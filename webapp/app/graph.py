"""Continuous LangGraph run — one graph.invoke() drives a story from
PLANNING through every chapter to COMPLETE, instead of a client repeatedly
POSTing /advance. SQLAlchemy/SQLite stays the source of truth: graph state
carries only story_id, and every node re-reads/writes the DB directly via
orchestrator.py's shared step functions. No LangGraph checkpointer is used —
resuming a crashed run is just re-invoking the graph, which re-derives
"what's next" from Story.phase / Chapter.status exactly like /advance does.
"""

import logging
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph
from openai import APIError

from . import orchestrator
from .db.models import Story
from .db.session import SessionLocal

logger = logging.getLogger(__name__)

_RETRY_DELAYS = (5, 20, 60)  # seconds; bounded backoff for 429/5xx from the provider

_WORK_STEPS = [
    "story_bible",
    "plot_outline",
    "characters",
    "world",
    "verify_planning",
    "planning_complete",
    "checkpoint",
    "blueprint",
    "write_chapter",
    "complete",
]


class GraphState(TypedDict):
    story_id: int
    last_step: str | None


def _run_step_with_retry(step: str, session, story: Story) -> None:
    """Runs the named step, retrying transient provider errors.

    On retry, the step is re-derived from _decide_next_step rather than
    blindly re-invoked: a step can partially commit before failing (e.g.
    write_chapter commits the chapter before summarizing it), so a naive
    retry could re-run stale logic against a chapter that already moved on.
    Re-deciding always dispatches whatever is actually next per DB state.
    """
    remaining = list(_RETRY_DELAYS)
    while True:
        executor = orchestrator._STEP_EXECUTORS[step]
        try:
            executor(session, story)
            return
        except APIError as exc:
            session.rollback()
            if not remaining:
                raise
            delay = remaining.pop(0)
            logger.warning("Step %s failed (%s), retrying in %ss", step, exc, delay)
            time.sleep(delay)
            step = orchestrator._decide_next_step(session, story)


def _make_node(step: str):
    def node(state: GraphState) -> dict:
        with SessionLocal() as session:
            story = session.get(Story, state["story_id"])
            _run_step_with_retry(step, session, story)
        return {"last_step": step}

    return node


def _decide(state: GraphState) -> str:
    with SessionLocal() as session:
        story = session.get(Story, state["story_id"])
        return orchestrator._decide_next_step(session, story)


def _build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("router", lambda state: state)
    graph.set_entry_point("router")

    for step in _WORK_STEPS:
        graph.add_node(step, _make_node(step))
        if step == "complete":
            graph.add_edge(step, END)
        else:
            graph.add_edge(step, "router")

    graph.add_conditional_edges("router", _decide, {step: step for step in _WORK_STEPS})
    return graph.compile()


_COMPILED_GRAPH = _build_graph()


def run_story_to_completion(story_id: int) -> None:
    # Default recursion_limit (25) is far too low: a 25-chapter novel is
    # roughly 2 * (4 planning + 2*25 writing + 5 checkpoints) =~ 120+ hops.
    _COMPILED_GRAPH.invoke(
        {"story_id": story_id, "last_step": None},
        config={"recursion_limit": 10000},
    )
