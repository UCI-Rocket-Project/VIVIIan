# UCIRPLGUI

`UCIRPLGUI` is a standalone scaffold for a VIVIIan-based operator desk.
It is intentionally thin: the files here define names, directories, and
run paths without implementing the real telemetry source, transport layer,
or GUI layout yet.

## Initial Shape

- `backend_sim_device.py` is the backend device-interface stub.
- `frontend_operator_desk.py` is the operator-facing GUI stub.
- `dashboard.py` is the only place where GUI composition should grow.
- `pipeline.py` describes the intended backend-to-frontend wiring.
- `scripts/` contains small launcher scripts that only validate imports and
  print next-step instructions.

## Directory Layout

```text
UCIRPLGUI/
  README.md
  scripts/
    run_backend.py
    run_frontend.py
    run_pipeline.py
  src/
    ucirplgui/
      __init__.py
      config.py
      device_interfaces/
        __init__.py
        backend_sim_device.py
        frontend_operator_desk.py
      components/
        __init__.py
        dashboard.py
      runtime/
        __init__.py
        pipeline.py
  tests/
    __init__.py
    test_scaffold_imports.py
```

## Install

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[gui]"
```

## Run The Scaffold

From the repo root:

```bash
PYTHONPATH=src:UCIRPLGUI/src python UCIRPLGUI/scripts/run_backend.py
PYTHONPATH=src:UCIRPLGUI/src python UCIRPLGUI/scripts/run_frontend.py
PYTHONPATH=src:UCIRPLGUI/src python UCIRPLGUI/scripts/run_pipeline.py
```

## Run The Scaffold Test

```bash
PYTHONPATH=src:UCIRPLGUI/src python -m unittest UCIRPLGUI/tests/test_scaffold_imports.py
```

## Planned Stream Contract

- telemetry read streams:
  - `signal_stream`
  - `pressure_stream`
- frontend write stream:
  - `ui_state`

## Next Implementation Steps

- Replace the simulated backend TODOs with a real source task or a wrapped
  `viviian.deviceinterface.DeviceInterface`.
- Add the first `SensorGraph`, `AnalogNeedleGauge`, `ToggleButton`, and
  `MomentaryButton` in `dashboard.py` and `frontend_operator_desk.py`.
- Wire the real `pythusa.Pipeline` stream definitions in `runtime/pipeline.py`.
- Register your custom theme in `src/viviian/gui_utils/theme.py` and replace
  the temporary `tau_ceti` theme default in this scaffold.
