# VIVIIan

VIVIIan is a public-facing Python telemetry and control monorepo for hardware-oriented systems.

## Repository Structure

- `packages/viviian_core/src/viviian` — core reusable toolkit modules.
- `packages/pythusa` — pipeline runtime dependency kept in-repo.
- `apps/ucirplgui` — operator-facing runtime application.
- `docs` — canonical documentation site (MkDocs).
- `tests` — repository-level regression tests.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[gui]
python apps/ucirplgui/scripts/run_all.py
```

## Documentation

- Start at `docs/index.md`
- Serve locally with `mkdocs serve`

## Community Standards

See:

- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `CHANGELOG.md`
