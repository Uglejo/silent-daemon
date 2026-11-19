from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sceneforge.schema.messages import InputItem


class ReasoningEffort(str, Enum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class Reasoning(BaseModel):
    effort: ReasoningEffort


class FunctionTool(BaseModel):
    type: Literal["function"] = "function"
    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = False


class ResponsesRequest(BaseModel):
    tools: list[FunctionTool] = []
    model: str
    reasoning: Reasoning
    max_output_tokens: int
    instructions: str
    input: list[InputItem]
    text: dict[str, Any] | None = Field(default=None)


class TokenUsage(BaseModel):
    model_config = ConfigDict(strict=True)

    input_tokens: int
    output_tokens: int
