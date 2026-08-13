"""Infrastructure with no story knowledge: configuration, the market contract,
object storage, JSON-mode LLM calls, slugs, schema hints.

Nothing here imports from `services` or `agents` — the dependency runs one way, so
a change to how a novel is built cannot reach down and alter how the app is wired.
"""
