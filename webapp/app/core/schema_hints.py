"""Compact, human-readable schema hints derived from Pydantic models.

Single source of truth for "what shape is this JSON": the Pydantic model. Prompts
reference a placeholder ({{plot_outline_schema}}) and prompts.loader substitutes
the generated hint — so changing a model updates every prompt automatically, with
no schema text duplicated across .md files.

compact_schema(PlotOutlineOut) →
  {title, arc_overview, chapters: [{number, title, act, target_words,
   arc_position, goals[], scenes: [{id, name, location, characters[],
   what_happens, scene_end}], character_notes, plot_threads[], cliffhanger}]}
"""
import json
import types
import typing

from pydantic import BaseModel

from ..schemas import PlotOutlineOut, WorldBibleOut


def _is_model(t) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


def _field_repr(name: str, annotation) -> str:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    # Optional[X] / X | None → describe X
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _field_repr(name, non_none[0])

    if origin in (list, tuple):
        inner = args[0] if args else None
        if _is_model(inner):
            return f"{name}: [{_model_body(inner)}]"
        return f"{name}[]"

    if _is_model(annotation):
        return f"{name}: {_model_body(annotation)}"

    return name


def _model_body(model: type[BaseModel]) -> str:
    parts = [_field_repr(n, f.annotation) for n, f in model.model_fields.items()]
    return "{" + ", ".join(parts) + "}"


def compact_schema(model: type[BaseModel]) -> str:
    """A one-line `{field, nested: {...}, list[]}` skeleton of a Pydantic model.

    Field names only — for describing an INPUT the agent reads (it just needs the
    shape). For an agent's OUTPUT contract use annotated_schema()."""
    return _model_body(model)


# ── JSON-example schema — the contract as READABLE JSON ───────────────────────
# Generated from the model, with each field's value = its Field(description=...),
# so the model is the single source of truth for BOTH shape and per-field guidance
# AND it reads as real JSON (easy to eyeball). A prompt says `{{schema:PlotOutlineOut}}`
# and gets this JSON block; change the model → every prompt using it updates.

def _unwrap_optional(annotation):
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return non_none[0]
    return annotation


def _example_obj(model: type[BaseModel]) -> dict:
    return {name: _example_value(f.annotation, f.description)
            for name, f in model.model_fields.items()}


def _example_value(annotation, description: str | None):
    ann = _unwrap_optional(annotation)
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin in (list, tuple):
        inner = args[0] if args else None
        if _is_model(inner):
            return [_example_obj(inner)]
        return [description or ""]
    if _is_model(ann):
        return _example_obj(ann)
    return description or ""


def json_schema(model: type[BaseModel]) -> str:
    """Readable JSON example of a model — valid JSON where each value is that
    field's description. Single source of truth = the Pydantic model."""
    return json.dumps(_example_obj(model), ensure_ascii=False, indent=2)


def _resolve_schema_placeholders(text: str) -> str:
    """Replace `{{schema:ModelName}}` (ModelName resolved from app.schemas) and the
    two legacy input-hint placeholders — all render as readable JSON examples."""
    import re

    from .. import schemas as _schemas

    text = text.replace("{{plot_outline_schema}}", json_schema(PlotOutlineOut))
    text = text.replace("{{world_bible_schema}}", json_schema(WorldBibleOut))

    def _sub(m: "re.Match") -> str:
        model = getattr(_schemas, m.group(1), None)
        if model is None or not _is_model(model):
            return m.group(0)  # unknown → leave as-is (visible, easy to catch)
        return json_schema(model)

    return re.sub(r"\{\{schema:(\w+)\}\}", _sub, text)
