from __future__ import annotations

from pythusa import Pipeline
import networkx as nx 
from dataclasses import dataclass



"""
def main() -> None:
    with VIVIIan.backend as VIVII:
        id1 = "test"
        stream1 = VIVII.add_ReceiveConnector(StreamSpec, "DataIngressStream")
        stream1.hash == some hash for the thing
        stream1.name == "DataIngressStream"
        # name is optional and when not used we fall back to the hash
        stream2 = VIVII.add_ReceiveConnector(StreamSpec, stream_X_id)
        VIVII.add_ReceiveConnector(StreamSpec)

        # the user is responsible for writing it with pythusa compatible tasks

        task1 = VIVII.add_task(some processing function, consumes from [stream1], task_name)
        task2 = VIVII.add_task(some processing function, consumes from [stream2, task1])
        VIVII.add_SendConnector(StreamSpec, consumes from, sendconnectortaskid)
        VIVII.add_SendConnector(StreamSpec, consumes from task id one, task id two ....
"""

@dataclass
class taskSpec: 
    runnable: function 
    name: str
    connections: list[str]

class Orchestrator:
    """
    Minimal orchestrator scaffold.

    This class intentionally wraps a local ``pythusa.Pipeline`` and owns only
    context-manager lifecycle boilerplate for now.
    """


    def __init__(self, name: str = "orchestrator", pipeline: Pipeline | None = None) -> None:
        self.name = name
        self.internal_graph = nx.DiGraph()
        self.pipeline = pipeline if pipeline is not None else Pipeline(name)
        self._closed = False

    def __enter__(self) -> "Orchestrator":
        self._ensure_open()
        self.pipeline.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.pipeline.__exit__(exc_type, exc, exc_tb)
        self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self.pipeline.close()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Orchestrator is closed.")
        
    def add_internal_graph(
        self,
        name: str,
        connected_to: set[str] | None = None,
    ) -> None:
        self._ensure_open()
        if not name:
            raise ValueError("name must be non-empty.")

        self.internal_graph.add_node(name)

        if not connected_to:
            return

        for upstream in connected_to:
            if not upstream:
                raise ValueError("connected_to entries must be non-empty.")
            self.internal_graph.add_node(upstream)
            self.internal_graph.add_edge(upstream, name)






__all__ = ["Orchestrator"]
