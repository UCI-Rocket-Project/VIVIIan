# Worker Testing

Prerequisite:

```powershell
pip install numpy
```

Run worker tests from the repository root:

```powershell
python -m unittest discover -s src/data_handeling/workers/testing -p "test_*.py" -v
```

If your package dependencies are installed, you can also run by module path:

```powershell
python -m unittest src.data_handeling.workers.testing.test_worker_basic -v
```
