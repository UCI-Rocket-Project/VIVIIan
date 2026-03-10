# Manager Testing

Prerequisite:

```powershell
pip install numpy
```

Run manager tests from the repository root:

```powershell
python -m unittest discover -s src/data_handeling/manager/testing -p "test_*.py" -v
```

If your package dependencies are installed, you can also run by module path:

```powershell
python -m unittest src.data_handeling.manager.testing.test_manager_basic -v
```
