from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from storyboardctl.errors import Conflict, NotFound
from storyboardctl.models import RenderMode


@dataclass(frozen=True)
class WorkflowContext:
    prompt: str
    render_mode: RenderMode
    width: int
    height: int
    frames: int
    steps: int
    seed: int
    output_key: str
    reference_images: tuple[str, ...] = ()
    first_frame: str | None = None
    last_frame: str | None = None


class WorkflowAdapter(Protocol):
    name: str

    def build_workflow(self, context: WorkflowContext) -> dict[str, dict[str, Any]]: ...

    def scrub_workflow(self, workflow: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]: ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, WorkflowAdapter] = {}

    def register(self, adapter: WorkflowAdapter) -> None:
        if adapter.name in self._adapters:
            raise Conflict(f"workflow adapter already registered: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> WorkflowAdapter:
        try:
            return self._adapters[name]
        except KeyError as error:
            raise NotFound(f"workflow adapter not found: {name}") from error

