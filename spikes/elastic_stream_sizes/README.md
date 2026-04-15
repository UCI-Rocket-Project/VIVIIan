## Elastic Stream Size Spike

This spike shows the smallest possible way to read a `pythusa` stream at a
different local frame size without changing core `pythusa`.

The idea is simple:

- the ring only moves bytes
- `StreamReader.look()` and `StreamReader.increment()` use `frame_nbytes`
- one reader can override `frame_nbytes` locally and consume a different amount
  of data per read

This is the standard procedure for this kind of size change with the current
runtime:

- keep the underlying stream normal
- override `frame_nbytes` on the local binding that wants a different size
- use `look()` / `increment()` on that resized side
- manually interpret the returned bytes with `np.frombuffer(...)` and the local
  shape you want

If you do this, one side has to handle the regrouping internally. The ring does
not preserve higher-level frame boundaries for you. If neither side owns that
regrouping logic, you can consume the byte stream at the wrong local size and
effectively lose data alignment.

This spike intentionally uses `look()` and `increment()` on the resized side.
It does **not** try to make `read()` or `read_into()` work with the overridden
size because those helpers also depend on the binding's `shape` and
`frame_size`.

Run the example:

```bash
PYTHONPATH=pythusa/src /usr/bin/python3 spikes/elastic_stream_sizes/example.py
```

Run the tests:

```bash
PYTHONPATH=pythusa/src /usr/bin/python3 -m unittest spikes.elastic_stream_sizes.test_elastic_stream_sizes
```
