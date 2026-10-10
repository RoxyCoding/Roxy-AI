"""Structured condition and stage reports embedded in assistant messages."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelingBrief(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=2000)
    target: str = ""
    use: str = ""
    shape: str = ""
    dimensions: str = ""
    materials: str = ""
    colors: str = ""


Stage = Literal["shape", "details", "materials", "finish"]


class ModelingProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["shape", "details", "materials", "finish", "done"]
    completed: list[Stage] = Field(default_factory=list, max_length=4)
    detail: str = Field(default="", max_length=300)


REPORTS = re.compile(r"^```roxy-(brief|progress|stage)[ \t]*\r?\n(.*?)^```[ \t]*(?:\r?\n|$)", re.M | re.S)
HIDDEN_REPORTS = re.compile(r"^```roxy-(?:brief|progress|stage)[ \t]*\r?\n.*?(?:^```[ \t]*(?:\r?\n|$)|\Z)", re.M | re.S)


class DesignStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: Literal["concept", "sheet"]


# Blender prompts in the design chat; only allowed once the concept image is approved.
MODELING_BLOCKS = re.compile(r"^```modeling[ \t]*\r?\n.*?(?:^```[ \t]*(?:\r?\n|$)|\Z)", re.M | re.S)


def strip_early_modeling(text: str, allowed_from: int | None) -> str:
    """Drop ```modeling blocks that start before the concept image was approved."""
    def keep(m: re.Match) -> str:
        return m[0] if allowed_from is not None and m.start() >= allowed_from else ""
    return MODELING_BLOCKS.sub(keep, text)


def visible_text(text: str) -> str:
    cleaned = HIDDEN_REPORTS.sub("", text)
    return cleaned.strip() if cleaned != text else text
