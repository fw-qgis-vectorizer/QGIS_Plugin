# -*- coding: utf-8 -*-
"""Built-in prompts for AI edit (/qgis/edit) workflows."""

# Obstacle removal only — prepended to the user's instruction; not shown in the UI.
# Solar panels use SOLAR_PANELS_PROMPT alone (no obstacle system prompt).
OBSTACLE_REMOVAL_SYSTEM_PROMPT = (
    "When inpainting the editing area, make it as close and clean as possible "
    "to the neighbouring and surrounding areas. "
    "Keep the editing area as close as possible to the obstacle itself."
)

# Used for solar panel placement — not shown in the UI.
SOLAR_PANELS_PROMPT = (
    "Add realistic rooftop solar panels covering the masked roof area. "
    "Match the aerial imagery style, lighting, and perspective. "
    "Do not change anything outside the masked polygon."
)


def build_edit_prompt(user_prompt: str, system_prompt: str | None = None) -> str:
    """Combine a hidden system prompt with the user instruction (obstacle removal only)."""
    user = (user_prompt or "").strip()
    system = (system_prompt or "").strip()
    if system and user:
        return f"{system}\n\n{user}"
    return user or system
