## Sequenced Stream Throughput Spike

This spike is the right place to answer the question:

- can a local `pythusa` stream carry `100000` distinct frames
- without loss or reordering
- at throughput consistent with an in-process shared-memory ring

This is intentionally **not** a connector test.

Why:

- `SendConnector` is a latest-batch Arrow Flight transport
- latest-batch transport and lossless sequenced streaming are different contracts
- forcing `100000` acknowledged frames through the connector unit suite measures
  orchestration latency, not the local stream's real throughput envelope

This spike keeps the contract clean:

- writer publishes one sequenced frame per write into a real `pythusa` ring
- reader consumes every frame in order using `look()` / `increment()`
- validation checks all `100000` distinct payloads
- the script reports elapsed time and frames per second

Run the benchmark:

```bash
PYTHONPATH=pythusa/src /usr/bin/python3 spikes/sequenced_stream_throughput/example.py
```

Enforce a local subsecond target:

```bash
PYTHONPATH=pythusa/src /usr/bin/python3 spikes/sequenced_stream_throughput/example.py --count 100000 --max-seconds 1.0
```

Run the spike test:

```bash
PYTHONPATH=pythusa/src /usr/bin/python3 -m unittest spikes.sequenced_stream_throughput.test_sequenced_stream_throughput
```
