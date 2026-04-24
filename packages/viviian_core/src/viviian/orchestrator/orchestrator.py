from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
from pythusa import Pipeline


@dataclass(slots=True)
class TaskSpec:
    runnable: object
    name: str
    connections: set[str] | None = None


class Orchestrator(Pipeline):
    """Thin Pipeline subclass for deployment-local composition."""

    def __init__(self, name: str = "orchestrator") -> None:
        super().__init__(name)
        self.internal_graph = nx.DiGraph()
        self.nodes: dict[str, TaskSpec] = {}

    def __enter__(self) -> "Orchestrator":
        super().__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        super().__exit__(exc_type, exc, exc_tb)

    def add_internal_graph(
        self,
        name: str,
        connected_to: set[str] | None = None,
    ) -> "Orchestrator":
        self._ensure_open()
        if not name:
            raise ValueError("name must be non-empty.")

        self.internal_graph.add_node(name)
        for upstream in connected_to or ():
            if not upstream:
                raise ValueError("connected_to entries must be non-empty.")
            self.internal_graph.add_node(upstream)
            self.internal_graph.add_edge(upstream, name)
        return self

    def _register_task(self, task_spec: TaskSpec) -> None:
        self.nodes[task_spec.name] = task_spec
        self.add_internal_graph(task_spec.name, task_spec.connections)


VIVIIan = Orchestrator

__all__ = ["Orchestrator", "TaskSpec", "VIVIIan"]
