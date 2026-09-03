"""Turn Pydantic models into schemas that constrained decoders actually accept.

Pydantic emits idiomatic JSON Schema; strict structured-output modes want a narrower
dialect. The differences are small but each one is a hard 400 from the provider:

* every object must set ``additionalProperties: false``
* every property must appear in ``required`` - optionality is expressed by allowing
  ``null`` in the type, not by omitting the key
* ``default``, ``format`` and numeric bounds are unsupported keywords and must be dropped

The original project sent a hand-written YAML block with the literal strings ``string``
and ``number`` as values. That is not a JSON Schema at all, so the ``response_format``
was silently ignored and every response came back as free-form prose that the caller then
tried to rescue with ``content.find("{")``. Deriving the schema from the model removes
that entire failure mode.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

__all__ = ["schema_fingerprint", "to_strict_json_schema"]

#: Keywords a strict decoder rejects outright.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "patternProperties",
        "uniqueItems",
    }
)


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a strict-mode JSON Schema for ``model``.

    The returned schema keeps ``$defs``/``$ref`` intact - every strict implementation in
    use supports references, and inlining them would blow up the prompt for a model with
    as many repeated sub-objects as a resume.
    """
    raw = model.model_json_schema(mode="serialization")
    tightened = _tighten(raw)
    if not isinstance(tightened, dict):  # pragma: no cover - a model schema is an object
        msg = f"{model.__name__} did not produce an object schema"
        raise TypeError(msg)
    return tightened


def _tighten(node: Any) -> Any:
    """Recursively apply the strict-dialect rules to one schema node."""
    if isinstance(node, list):
        return [_tighten(item) for item in node]
    if not isinstance(node, dict):
        return node

    result: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_KEYWORDS:
            continue
        if key == "$defs" or key in ("properties", "definitions"):
            result[key] = {name: _tighten(sub) for name, sub in value.items()}
        else:
            result[key] = _tighten(value)

    if result.get("type") == "object" or "properties" in result:
        properties = result.get("properties", {})
        result["additionalProperties"] = False
        # Strict mode has no notion of an optional key, so every property is listed as
        # required. Nullability is *not* added here: Pydantic already emits
        # `anyOf: [..., {"type": "null"}]` for every `X | None` field, and widening the
        # rest would let a model answer `null` where the domain model has no such state -
        # a null `contact` or a null `Skill.name` would fail validation on arrival. A
        # missing object is `{}` and a missing list is `[]`; both are already expressible.
        result["required"] = list(properties.keys())

    return result


def schema_fingerprint(schema: dict[str, Any]) -> str:
    """Stable short digest of a schema, used as part of the cache key.

    Changing the schema must invalidate cached parses, otherwise a deployment that adds a
    field keeps serving stale results that lack it.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
