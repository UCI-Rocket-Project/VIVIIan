# Simulation Utils

`simulation_utils` contains the current top-level signal simulator stack.
Its job is simple:

- define one exact repeating cycle in NumPy `rfft` space
- reconstruct the time-domain signal deterministically
- emit `(2, rows)` timestamp/value batches that can feed the GUI graph path

This is useful for building telemetry-like signals that are:

- deterministic
- compact to serialize
- reconstructable from config
- easy to batch into a graph or a future pipeline

## Mental Model

Each signal is defined by a sparse set of `rfft` bins.
From that sparse spectral config, the runtime:

1. builds a dense `rfft` vector
2. reconstructs one exact time-domain cycle with `np.fft.irfft(...)`
3. applies `offset + scale * base_cycle`
4. serves samples from that cycle forever with wraparound

The repeat interval is:

```python
samples_per_cycle / sample_rate_hz
```

## Core Types

### SpectralTerm

`SpectralTerm` is one `rfft` coefficient:

| Field | Meaning |
| --- | --- |
| `bin_index` | non-negative frequency bin |
| `real` | real component |
| `imag` | imaginary component |

Validation rules follow NumPy `rfft` conventions:

- `bin_index >= 0`
- DC bin `0` must have `imag == 0`
- the Nyquist bin for even-length cycles must have `imag == 0`

### SpectralSignalConfig

`SpectralSignalConfig` is the serializable definition of one signal:

| Field | Meaning |
| --- | --- |
| `signal_id` | config identifier |
| `sample_rate_hz` | sample rate |
| `samples_per_cycle` | exact cycle length in samples |
| `terms` | sparse tuple of `SpectralTerm` |
| `offset` | additive post-reconstruction offset |
| `scale` | multiplicative post-reconstruction scale |
| `output_dtype` | currently fixed to `float64` |

Useful helpers:

- `dt_s()`
- `cycle_duration_s()`
- `dense_spectrum()`
- `cycle_values()`
- `cycle_timestamps()`
- `cycle_series()`
- `build_generator()`
- `export(...)`
- `reconstruct(...)`

### SpectralSignalGenerator

`SpectralSignalGenerator` is the stateful runtime object.
It caches one cycle and advances a running sample index.

Important methods:

- `cycle_values()`
- `cycle_series()`
- `next_batch(rows)`
- `iter_batches(rows)`
- `reset(sample_index=0)`

The batch shape is always:

```python
(2, rows)
```

with:

- row `0`: timestamps
- row `1`: values

## Hand-Authored Signal Example

```python
from viviian.simulation_utils import SpectralSignalConfig, SpectralTerm

config = SpectralSignalConfig(
    signal_id="copv_pressure",
    sample_rate_hz=128.0,
    samples_per_cycle=1024,
    terms=(
        SpectralTerm(bin_index=3, real=4.0, imag=-1.0),
        SpectralTerm(bin_index=9, real=1.5, imag=0.5),
    ),
    offset=50.0,
    scale=2.0,
)

print(config.cycle_duration_s())
print(config.cycle_series().shape)
```

This builds one exact cycle, then repeats it forever once you construct a generator.

## Generator Example

```python
generator = config.build_generator()

batch = generator.next_batch(8)
print(batch.shape)      # (2, 8)
print(batch[0])         # timestamps
print(batch[1])         # values
```

Because the generator carries `sample_index`, repeated calls continue forward in time:

```python
batch_a = generator.next_batch(4)
batch_b = generator.next_batch(4)
```

and once it reaches the end of the cycle, it wraps back to the start of the cached signal values while timestamps continue monotonically.

## TOML Export And Reconstruction

The simulator config is intentionally compact.
Only nonzero spectral terms are stored.

```python
path = config.export("configs/copv_signal.toml")
rebuilt = SpectralSignalConfig.reconstruct(path)
```

That TOML stores:

- signal metadata
- sample rate
- samples per cycle
- offset and scale
- one `[[terms]]` table per sparse spectral bin

## Seeded Sparse Helper

The fastest way to create plausible signals for demos is:

```python
from viviian.simulation_utils import random_sparse_spectrum_generator

generator = random_sparse_spectrum_generator(
    signal_id="signal_1",
    sample_rate_hz=256.0,
    samples_per_cycle=2048,
    seed=42,
    nonzero_terms=6,
    min_bin=1,
    max_bin=64,
    coefficient_scale=4.0,
    noise_floor_std=0.03,
)
```

### Helper Parameters

| Parameter | Meaning |
| --- | --- |
| `signal_id` | identifier |
| `sample_rate_hz` | output sample rate |
| `samples_per_cycle` | exact cycle size |
| `seed` | RNG seed for reproducibility |
| `nonzero_terms` | number of nonzero spectral bins |
| `min_bin` / `max_bin` | bin selection range |
| `coefficient_scale` | spectral coefficient magnitude scale |
| `offset` | additive shift after reconstruction |
| `scale` | multiplicative scale after reconstruction |
| `noise_floor_std` | deterministic periodic noise folded back into the final spectrum |

The helper is deterministic for a fixed seed.
If you call it twice with the same arguments, you get the same config and the same cycle values.

## Notes On NumPy `rfft`

These simulators follow NumPy’s `rfft` layout:

- only nonnegative frequency bins are stored
- the DC bin is real-only
- the Nyquist bin is real-only when `samples_per_cycle` is even

That is why the config validation rejects:

- negative bins
- out-of-range bins
- imaginary DC
- imaginary Nyquist

## Example: Exact Repetition

```python
generator = random_sparse_spectrum_generator(
    signal_id="demo",
    sample_rate_hz=64.0,
    samples_per_cycle=16,
    seed=19,
    nonzero_terms=3,
    coefficient_scale=1.0,
    noise_floor_std=0.15,
)

batch = generator.next_batch(32)
assert (batch[1, :16] == batch[1, 16:]).all()
```

The signal can look noisy, but it is still an exact repeating cycle because the noise is baked back into the final spectral representation.

## Where It Is Used Today

The best current reference is the manual GUI example:

```bash
python tests/gui_runnables/signal_graph_lab.py
```

That example builds a bank of eight seeded spectral generators and feeds their batches into `SensorGraph` through lightweight reader adapters.
