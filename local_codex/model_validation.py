"""Validation shared by setup and the Ollama runtime boundary."""

from typing import Any
from .i18n import tr


def validate_model_info(model: str, role: str, info: dict[str, Any], reasoning: str) -> None:
    if info.get("remote_host") or info.get("remote_model"):
        raise ValueError(tr("setup.cloud_model", model=model))
    capabilities = info.get("capabilities", [])
    required = ["completion", "tools"]
    if role == "vision":
        required.append("vision")
    if reasoning != "none":
        required.append("thinking")
    for capability in required:
        if capability not in capabilities:
            raise ValueError(tr("setup.capability", model=model, capability=capability))
