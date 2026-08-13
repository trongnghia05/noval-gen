"""Everything that talks to a language model.

`base` is the interface agents are written against, `openrouter` and `vertex` are
the two implementations, and `structured` is the JSON-mode wrapper that validates a
reply against a Pydantic schema. It lives here rather than in `core` because it is
part of the LLM boundary, not general infrastructure — an agent that wants a typed
answer and an agent that wants raw prose reach into the same package.
"""
