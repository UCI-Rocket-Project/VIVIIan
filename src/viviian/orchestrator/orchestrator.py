from __future__ import annotations

from pythusa import Pipeline
import networkx as nx 
from dataclasses import dataclass



"""
def main() -> None:
    with VIVIIan("backend") as VIVII:
        id1 = "test"
        stream1 = VIVII.add_ReceiveConnector(StreamSpec, "DataIngressStream")
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
class TaskSpec: 
    runnable: function 
    name: str
    connections: set[str] | None

class Orchestrator(Pipeline):
    """s
    Minimal orchestrator scaffold.

    This class intentionally wraps a local ``pythusa.Pipeline`` and owns only
    context-manager lifecycle boilerplate for now.
    """


    def __init__(self, name: str = "orchestrator") -> None:
        super().__init__(name)
        self.internal_graph = nx.DiGraph()
        self.nodes: dict[str, TaskSpec] = {}

    def __enter__(self) -> "Orchestrator":
        super().__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        super().__exit__(exc_type, exc, exc_tb)

    def close(self) -> None:
        super().close()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Orchestrator is closed.")
        

    #adds tasks and connectors to the internal graph to that orchestrator can compile to pythusa pipeline
    #streams and other tasks
    def _add_to_internal_graph(
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

    def _register_task(self, task_spec: TaskSpec) -> None: 
        self.nodes[task_spec.name] = task_spec 
        self._add_to_internal_graph(task_spec.name, connected_to=task_spec.connections)

    def _compile_to_pipeline(self) -> None:
        for edge in self.internal_graph.edges():
            #finish implementing here
            name = edge[0] + edge[1]

            self.add_stream(name= name, ) 
        
        for node in self.internal_graph.nodes():
            #finish implementing here
            self.add_task(node)



    

    def compile(self) -> None:
        self._ensure_open()
        if self._compiled:
            raise RuntimeError("Pipeline has already been compiled.")
        self._compile_to_pipeline()
        super().compile()


__all__ = ["Orchestrator"]
