"""Worker definitions."""

from .worker import EventSpec, TaskSpec, Worker, WorkerSpec

# Compatibility alias for older imports that still refer to AbstractWorker.
AbstractWorker = Worker
CallType = None
WorkerState = None

__all__ = [
    "Worker",
    "AbstractWorker",
    "TaskSpec",
    "WorkerSpec",
    "EventSpec",
    "CallType",
    "WorkerState",
]
