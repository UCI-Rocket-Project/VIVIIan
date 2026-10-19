# VIVIIan

VIVIIan is a public-facing Python telemetry and control monorepo for hardware-oriented systems.

## Repository Structure

- `packages/viviian_core/src/viviian` — core reusable toolkit modules.
- `packages/pythusa` — pipeline runtime dependency kept in-repo.
- `apps/GUI2.1` — local GSE operator scripts.
- `tests/gui_runnables` — runnable desks for the shared GUI and frontend runtime.
- `docs` — canonical documentation site (MkDocs).
- `tests` — repository-level regression tests.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e "packages/pythusa" -e ".[gui]"
python tests/gui_runnables/signal_graph_lab.py
```

`pythusa` lives under `packages/pythusa` and must be installed as an editable package (or otherwise on `PYTHONPATH`) before imports like `from pythusa import Pipeline` will resolve.

## Documentation

- Start at `docs/index.md`
- Serve locally with `mkdocs serve`

## Community Standards

See:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `CHANGELOG.md`
