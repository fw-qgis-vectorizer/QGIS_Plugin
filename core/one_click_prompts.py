# -*- coding: utf-8 -*-
"""Point prompt tracking for one-click segmentation."""

from __future__ import annotations


class OneClickPromptManager:
    def __init__(self):
        self.positive_points: list[tuple[float, float]] = []
        self.negative_points: list[tuple[float, float]] = []
        self._history: list[tuple[str, tuple[float, float]]] = []

    def add_positive_point(self, x: float, y: float):
        self.positive_points.append((x, y))
        self._history.append(("positive", (x, y)))

    def add_negative_point(self, x: float, y: float):
        self.negative_points.append((x, y))
        self._history.append(("negative", (x, y)))

    def undo_last(self) -> tuple[str, tuple[float, float]] | None:
        if not self._history:
            return None
        label, point = self._history.pop()
        target = self.positive_points if label == "positive" else self.negative_points
        for i in range(len(target) - 1, -1, -1):
            if target[i] == point:
                target.pop(i)
                break
        return label, point

    def clear(self):
        self.positive_points = []
        self.negative_points = []
        self._history = []

    @property
    def total_points(self) -> int:
        return len(self.positive_points) + len(self.negative_points)
