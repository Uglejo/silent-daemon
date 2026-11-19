from __future__ import annotations

from typing import Any

FRONT_VIEWS = ("front_left", "front_right", "front_below", "front_above")


def _format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}


def brief_format() -> dict[str, Any]:
    text = {"type": "string"}
    texts = {"type": "array", "items": text}
    return _format("target_brief", {
        "type": "object",
        "additionalProperties": False,
        "required": ["main_object", "items", "features", "pose", "true_colors"],
        "properties": {"main_object": text, "items": texts, "features": texts, "pose": text, "true_colors": texts},
    })


def select_close_format(labels: list[str]) -> dict[str, Any]:
    fields = (*FRONT_VIEWS, "grid", "garbage_sides")
    return _format("candidate_judgement", {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates"],
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", *fields],
                    "properties": {"label": {"type": "string", "enum": labels},
                                   **{field: {"type": "integer"} for field in fields}},
                },
            }
        },
    })


def critic_format() -> dict[str, Any]:
    text = {"type": "string"}
    return _format("edit_plan", {
        "type": "object",
        "additionalProperties": False,
        "required": ["edits"],
        "properties": {
            "edits": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["part", "problem", "change"],
                    "properties": {"part": text, "problem": text, "change": text},
                },
            }
        },
    })


def critique_format(labels: list[str]) -> dict[str, Any]:
    texts = {"type": "array", "items": {"type": "string"}}
    label = {"type": "string", "enum": labels}
    return _format("draft_critique", {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates", "base"],
        "properties": {
            "base": label,
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "strengths", "weaknesses"],
                    "properties": {"label": label, "strengths": texts, "weaknesses": texts},
                },
            },
        },
    })
