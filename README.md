# GSE 2.0 NI-DAQ Reader

New NI-DAQ board reader and live telemetry visualization tooling for the UCI Rocket Project.

Current author and maintainer: **David Culciar**

## What This Repo Contains
- `nidaq_client/`: NI-DAQ acquisition and stream publisher scripts.
- `tui_client/`: ImGui-based live plotting/monitoring clients.
- `shared_config/`: Shared config loader/helpers.
- `gse2_0.toml`: Primary runtime configuration (stream, signals, graph layout, etc.).

## Quick Start
1. Create and activate a Python virtual environment.
2. Install dependencies used by the NI-DAQ and TUI clients.
3. Start the NI-DAQ stream publisher.
4. Start the TUI consumer/plotter.

Example run commands (from repo root):

```powershell
python .\nidaq_client\nidaq_quest_stream.py
python .\tui_client\network_nidaq_pipeline_test.py
```

Fake signal mode (for GUI testing without hardware):

```powershell
python .\nidaq_client\nidaq_quest_stream_fake.py
```

## Notes
- The TUI is config-driven; signal and graph-cell setup should be edited in `gse2_0.toml`.
- Keep acquisition/network throughput decoupled from rendering throughput for smooth UI behavior.
