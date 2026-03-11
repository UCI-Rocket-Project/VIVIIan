# VIVIIan API Example Scaffold

This folder is a blank workspace for writing a user-facing VIVIIan example.

## Files

- `main.py`
  Imports the orchestration-side API through `import viviian as vivii`.
- `tasks.py`
  Imports the task-side API through `import viviian as vivii` plus task-local deps.

## Intended split

- Put process orchestration in `main.py`.
- Put worker task functions in `tasks.py`.
- Register task functions from `tasks.py` inside `main.py`.

## Import style

Use:

```python
import viviian as vivii
```

Then reach the common API as:

```python
vivii.Manager
vivii.RingSpec
vivii.TaskSpec
vivii.EventSpec
vivii.SharedRingBuffer
vivii.context.get_reader("my_ring")
```

## Run

From the repository root:

```powershell
python -m examples.viviian_api_usage.main
```

Right now the scaffold only imports modules and exits. Add your example logic in place.
