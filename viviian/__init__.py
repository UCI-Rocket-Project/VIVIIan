from __future__ import annotations

from pathlib import Path

# Expose the existing src/ tree as the VIVIIan package namespace so
# spawned child processes can import VIVIIan.data_handeling...
_SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
__path__ = [str(_SRC_ROOT)]

