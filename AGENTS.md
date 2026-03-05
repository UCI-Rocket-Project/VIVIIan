# Repository Guidelines

## Project Structure & Module Organization
- Core code lives in `src/`.
- Data pipeline primitives are under `src/data_handeling/`:
  - `shared_ring_buffer.py` for shared-memory ring logic
  - `manager/` for process/event orchestration
  - `workers/` for worker bootstrap and runtime behavior
- Aggregation/network logic is in `src/data_aggregation/`.
- Performance experiments live in `benchmarking/` and `src/data_handeling/testing/test_shared_ring_buffer_throughput.py`.
- Tests are colocated in `*/testing/` folders and follow `test_*.py` naming.

## Build, Test, and Development Commands
- Run a specific test module:
  - `python -m unittest src.data_handeling.testing.test_shared_ring_buffer_basic -v`
- Run all ring-buffer tests:
  - `python -m unittest discover -s src/data_handeling/testing -p "test_shared_ring_buffer*.py" -v`
- Run manager tests:
  - `python -m unittest discover -s src/data_handeling/manager/testing -p "test_*.py" -v`
- Run playground script:
  - `python -m src.data_handeling.manager.testing.manager_playground`

## Coding Style & Naming Conventions
- Use Python 3 style with 4-space indentation and type hints where practical.
- Keep modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE_CASE`.
- Prefer explicit shared-memory cleanup (`release()`, `close()`, `unlink()`) in long-running or multiprocessing code.
- No enforced formatter is configured in-repo; keep formatting consistent with surrounding code and validate with `py_compile` when needed.

## Testing Guidelines
- Framework: built-in `unittest`.
- Test files: `test_*.py`; test methods: `test_*`.
- Focus tests on ring-buffer invariants, wrap-around behavior, and reader/writer position correctness.
- For multiprocessing or shared-memory changes, include at least one regression test in the relevant `testing/` directory.

## Commit & Pull Request Guidelines
- Current history uses short, imperative, lowercase messages (e.g., `added gate`, `started bootstrap`).
- Keep commits scoped to one concern (buffer logic, worker lifecycle, tests, etc.).
- PRs should include:
  - what changed and why
  - affected modules/paths
  - test commands run and results
  - performance notes for throughput-related changes

## Configuration & Safety Notes
- Runtime config is in `gse2_0.toml` and `shared_config/`.
- Avoid hardcoding device/network settings in code; prefer config updates.
- Be careful with shared memory leaks: always close/unlink on shutdown paths.
