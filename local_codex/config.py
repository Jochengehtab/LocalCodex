"""Validated, read-only local model configuration; no runtime side effects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib


@dataclass(frozen=True)
class ModelProfile:
    source: str
    context: int = 8192
    reasoning: str = "xhigh"


@dataclass(frozen=True)
class ModelConfig:
    plan: ModelProfile = ModelProfile("qwen3.8:27b")
    build: ModelProfile = ModelProfile("qwen3.6:35b-a3b")
    vision: ModelProfile = ModelProfile("qwen3-vl:30b")

    def profiles(self) -> dict[str, ModelProfile]:
        return {role: getattr(self, role) for role in ("plan", "build", "vision")}


def load_model_config(path: Path) -> ModelConfig:
    if not path.exists():
        return ModelConfig()
    with path.open("rb") as source:
        data = tomllib.load(source)
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1 or set(data) - {"schema_version", "models"}:
        raise ValueError("LocalCodex config requires schema_version = 1 and [models.<role>] tables")
    models = data.get("models", {})
    if not isinstance(models, dict) or set(models) - {"plan", "build", "vision"}:
        raise ValueError("Unknown model role; expected plan, build or vision")
    profiles = ModelConfig().profiles()
    for role, values in models.items():
        if not isinstance(values, dict) or set(values) - {"source", "context", "reasoning"}:
            raise ValueError(f"Invalid model profile: {role}")
        default = profiles[role]
        model = values.get("source", default.source)
        context = values.get("context", default.context)
        reasoning = values.get("reasoning", default.reasoning)
        if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]*", model):
            raise ValueError(f"Invalid local model name: {role}")
        if (
            "://" in model
            or model.startswith("local-codex-")
            or model.endswith(":cloud")
            or "-cloud" in model
        ):
            raise ValueError(f"Expected a local source model, not an alias or cloud model: {role}")
        if type(context) is not int or not 2048 <= context <= 131072:
            raise ValueError(f"Context must be between 2048 and 131072: {role}")
        if reasoning not in ("none", "low", "medium", "high", "xhigh"):
            raise ValueError(f"Unsupported reasoning setting: {role}")
        profiles[role] = ModelProfile(model, context, reasoning)
    return ModelConfig(**profiles)
