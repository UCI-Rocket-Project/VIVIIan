# UCIRPLGUI

UCIRPLGUI is the operator-facing app in this monorepo.

## Location

- App root: `apps/ucirplgui`
- Runtime package: `apps/ucirplgui/src/ucirplgui`

## Run

From repository root:

```bash
python apps/ucirplgui/scripts/run_all.py
```

## Optional Real ECU Mode

```bash
python apps/ucirplgui/scripts/run_all.py --real-ecu --ecu-host <HOST> --ecu-port <PORT>
```

## Test

```bash
PYTHONPATH="packages/viviian_core/src:apps/ucirplgui/src" python -m unittest apps.ucirplgui.tests.test_dashboard_runtime
```

## Notes

- Device link status is published by device-interface processes directly to frontend-consumed JSON snapshots.
- Dashboard uses Tau-Ceti visual language by default.
