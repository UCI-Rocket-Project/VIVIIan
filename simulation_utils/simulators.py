from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Any, Iterator, Mapping, Sequence

import numpy as np

from .configure import (
    read_toml_document,
    render_finite_float,
    require_keys,
    require_kind,
    toml_header,
    toml_string,
    write_toml_document,
)

_SUPPORTED_OUTPUT_DTYPE = "float64"
_SPECTRUM_ZERO_TOLERANCE = 1.0e-12


@dataclass(frozen=True, slots=True)
class SpectralTerm:
    bin_index: int
    real: float
    imag: float

    def __post_init__(self) -> None:
        if isinstance(self.bin_index, bool) or not isinstance(self.bin_index, int):
            raise TypeError("SpectralTerm.bin_index must be an int.")
        if self.bin_index < 0:
            raise ValueError("SpectralTerm.bin_index must be non-negative.")
        if not math.isfinite(float(self.real)) or not math.isfinite(float(self.imag)):
            raise ValueError("SpectralTerm real and imag values must be finite.")
        object.__setattr__(self, "real", float(self.real))
        object.__setattr__(self, "imag", float(self.imag))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpectralTerm":
        require_keys(data, "spectral term", "bin_index", "real", "imag")
        return cls(
            bin_index=int(data["bin_index"]),
            real=float(data["real"]),
            imag=float(data["imag"]),
        )


@dataclass(frozen=True, slots=True)
class SpectralSignalConfig:
    signal_id: str
    sample_rate_hz: float
    samples_per_cycle: int
    terms: tuple[SpectralTerm, ...]
    offset: float = 0.0
    scale: float = 1.0
    output_dtype: str = _SUPPORTED_OUTPUT_DTYPE

    KIND = "spectral_signal"

    def __post_init__(self) -> None:
        if not self.signal_id:
            raise ValueError("signal_id must be non-empty.")
        if not math.isfinite(float(self.sample_rate_hz)) or float(self.sample_rate_hz) <= 0.0:
            raise ValueError("sample_rate_hz must be a finite float greater than 0.")
        if (
            isinstance(self.samples_per_cycle, bool)
            or not isinstance(self.samples_per_cycle, int)
            or self.samples_per_cycle <= 0
        ):
            raise ValueError("samples_per_cycle must be a positive integer.")
        if not math.isfinite(float(self.offset)) or not math.isfinite(float(self.scale)):
            raise ValueError("offset and scale must be finite.")
        if self.output_dtype != _SUPPORTED_OUTPUT_DTYPE:
            raise ValueError(
                f"output_dtype must be {_SUPPORTED_OUTPUT_DTYPE!r} in v1."
            )

        terms = tuple(self.terms)
        self._validate_terms(terms)
        object.__setattr__(self, "sample_rate_hz", float(self.sample_rate_hz))
        object.__setattr__(self, "offset", float(self.offset))
        object.__setattr__(self, "scale", float(self.scale))
        object.__setattr__(
            self,
            "terms",
            tuple(sorted(terms, key=lambda term: term.bin_index)),
        )

    def __repr__(self) -> str:
        bins = ",".join(str(term.bin_index) for term in self.terms)
        return (
            "SpectralSignalConfig("
            f"signal_id={self.signal_id!r}, "
            f"sample_rate_hz={self.sample_rate_hz!r}, "
            f"samples_per_cycle={self.samples_per_cycle!r}, "
            f"bins=[{bins}], "
            f"offset={self.offset!r}, "
            f"scale={self.scale!r})"
        )

    def dt_s(self) -> float:
        return 1.0 / self.sample_rate_hz

    def cycle_duration_s(self) -> float:
        return self.samples_per_cycle / self.sample_rate_hz

    def dense_spectrum(self) -> np.ndarray:
        spectrum = np.zeros(self.rfft_length(), dtype=np.complex128)
        for term in self.terms:
            spectrum[term.bin_index] = complex(term.real, term.imag)
        return spectrum

    def base_cycle_values(self) -> np.ndarray:
        return np.fft.irfft(self.dense_spectrum(), n=self.samples_per_cycle)

    def cycle_values(self) -> np.ndarray:
        values = self.base_cycle_values()
        if self.scale != 1.0:
            values = values * self.scale
        if self.offset != 0.0:
            values = values + self.offset
        return np.asarray(values, dtype=np.float64)

    def cycle_timestamps(self) -> np.ndarray:
        return np.arange(self.samples_per_cycle, dtype=np.float64) / self.sample_rate_hz

    def cycle_series(self) -> np.ndarray:
        return np.vstack((self.cycle_timestamps(), self.cycle_values()))

    def build_generator(self) -> "SpectralSignalGenerator":
        return SpectralSignalGenerator(self)

    def export(self, path: str | Path) -> Path:
        lines = toml_header(self.KIND)
        lines.extend(
            [
                f"signal_id = {toml_string(self.signal_id)}",
                f"sample_rate_hz = {render_finite_float(self.sample_rate_hz)}",
                f"samples_per_cycle = {self.samples_per_cycle}",
                f"offset = {render_finite_float(self.offset)}",
                f"scale = {render_finite_float(self.scale)}",
                f"output_dtype = {toml_string(self.output_dtype)}",
            ]
        )
        for term in self.terms:
            lines.extend(
                [
                    "",
                    "[[terms]]",
                    f"bin_index = {term.bin_index}",
                    f"real = {render_finite_float(term.real)}",
                    f"imag = {render_finite_float(term.imag)}",
                ]
            )
        lines.append("")
        return write_toml_document(path, "\n".join(lines).rstrip() + "\n")

    @classmethod
    def reconstruct(cls, path: str | Path) -> "SpectralSignalConfig":
        data = read_toml_document(path)
        require_kind(data, cls.KIND)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpectralSignalConfig":
        require_keys(
            data,
            cls.KIND,
            "signal_id",
            "sample_rate_hz",
            "samples_per_cycle",
            "offset",
            "scale",
            "output_dtype",
        )
        term_entries = data.get("terms", ())
        if isinstance(term_entries, Mapping):
            term_entries = [term_entries]
        if not isinstance(term_entries, Sequence):
            raise ValueError("terms must be a sequence of TOML tables.")

        terms = tuple(SpectralTerm.from_dict(entry) for entry in term_entries)
        return cls(
            signal_id=str(data["signal_id"]),
            sample_rate_hz=float(data["sample_rate_hz"]),
            samples_per_cycle=int(data["samples_per_cycle"]),
            terms=terms,
            offset=float(data["offset"]),
            scale=float(data["scale"]),
            output_dtype=str(data["output_dtype"]),
        )

    def rfft_length(self) -> int:
        return (self.samples_per_cycle // 2) + 1

    def _validate_terms(self, terms: tuple[SpectralTerm, ...]) -> None:
        max_bin = self.samples_per_cycle // 2
        seen_bins: set[int] = set()
        nyquist_bin = max_bin if self.samples_per_cycle % 2 == 0 else None

        for term in terms:
            if term.bin_index > max_bin:
                raise ValueError(
                    f"SpectralTerm bin_index {term.bin_index} exceeds rfft max bin {max_bin}."
                )
            if term.bin_index in seen_bins:
                raise ValueError(
                    f"Duplicate SpectralTerm bin_index {term.bin_index} is not allowed."
                )
            seen_bins.add(term.bin_index)

            if term.bin_index == 0 and term.imag != 0.0:
                raise ValueError("The DC bin must have imag == 0.0 for rfft compatibility.")
            if nyquist_bin is not None and term.bin_index == nyquist_bin and term.imag != 0.0:
                raise ValueError(
                    "The Nyquist bin must have imag == 0.0 for even-length rfft signals."
                )


class SpectralSignalGenerator:
    def __init__(self, config: SpectralSignalConfig) -> None:
        self.config = config
        self._cycle_values = config.cycle_values()
        self._cycle_timestamps = config.cycle_timestamps()
        self.sample_index = 0

    def __repr__(self) -> str:
        return (
            "SpectralSignalGenerator("
            f"signal_id={self.config.signal_id!r}, "
            f"samples_per_cycle={self.config.samples_per_cycle}, "
            f"sample_index={self.sample_index})"
        )

    def cycle_values(self) -> np.ndarray:
        return self._cycle_values.copy()

    def cycle_series(self) -> np.ndarray:
        return np.vstack((self._cycle_timestamps.copy(), self._cycle_values.copy()))

    def next_batch(self, rows: int) -> np.ndarray:
        if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
            raise ValueError("rows must be a positive integer.")

        sample_positions = np.arange(rows, dtype=np.int64) + self.sample_index
        cycle_index = np.mod(sample_positions, self.config.samples_per_cycle)
        timestamps = sample_positions.astype(np.float64) / self.config.sample_rate_hz
        values = self._cycle_values[cycle_index]
        self.sample_index += rows
        return np.vstack((timestamps, values))

    def iter_batches(self, rows: int) -> Iterator[np.ndarray]:
        while True:
            yield self.next_batch(rows)

    def reset(self, sample_index: int = 0) -> None:
        if isinstance(sample_index, bool) or not isinstance(sample_index, int) or sample_index < 0:
            raise ValueError("sample_index must be a non-negative integer.")
        self.sample_index = sample_index


class RotationMatrixSignalGenerator:
    """Generate a repeating row-major 3x3 rotation-matrix stream from roll/pitch/yaw signals."""

    def __init__(
        self,
        *,
        roll: SpectralSignalGenerator,
        pitch: SpectralSignalGenerator,
        yaw: SpectralSignalGenerator,
    ) -> None:
        configs = (roll.config, pitch.config, yaw.config)
        ref_rate = configs[0].sample_rate_hz
        ref_cycle = configs[0].samples_per_cycle
        rates_match = all(math.isclose(item.sample_rate_hz, ref_rate) for item in configs[1:])
        cycles_match = all(item.samples_per_cycle == ref_cycle for item in configs[1:])
        if not rates_match or not cycles_match:
            raise ValueError("roll, pitch, and yaw generators must share sample_rate_hz and samples_per_cycle.")
        self.roll = roll
        self.pitch = pitch
        self.yaw = yaw
        self.sample_rate_hz = float(roll.config.sample_rate_hz)
        self.samples_per_cycle = int(roll.config.samples_per_cycle)

    def __repr__(self) -> str:
        return (
            "RotationMatrixSignalGenerator("
            f"sample_rate_hz={self.sample_rate_hz}, "
            f"samples_per_cycle={self.samples_per_cycle}, "
            f"sample_index={self.sample_index})"
        )

    @property
    def sample_index(self) -> int:
        return int(self.roll.sample_index)

    def next_batch(self, rows: int) -> np.ndarray:
        if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
            raise ValueError("rows must be a positive integer.")
        roll_batch = self.roll.next_batch(rows)
        pitch_batch = self.pitch.next_batch(rows)
        yaw_batch = self.yaw.next_batch(rows)
        timestamps = roll_batch[0]
        matrices = np.empty((9, rows), dtype=np.float64)
        for column in range(rows):
            matrix = _rotation_matrix_from_euler_xyz(
                roll=float(roll_batch[1, column]),
                pitch=float(pitch_batch[1, column]),
                yaw=float(yaw_batch[1, column]),
            )
            matrices[:, column] = matrix.reshape(9)
        return np.vstack((timestamps, matrices))

    def iter_batches(self, rows: int) -> Iterator[np.ndarray]:
        while True:
            yield self.next_batch(rows)

    def reset(self, sample_index: int = 0) -> None:
        self.roll.reset(sample_index)
        self.pitch.reset(sample_index)
        self.yaw.reset(sample_index)


def random_sparse_spectrum_config(
    *,
    signal_id: str,
    sample_rate_hz: float,
    samples_per_cycle: int,
    seed: int,
    nonzero_terms: int,
    min_bin: int = 1,
    max_bin: int | None = None,
    coefficient_scale: float = 1.0,
    offset: float = 0.0,
    scale: float = 1.0,
    noise_floor_std: float = 0.0,
    allow_dc: bool = False,
    allow_nyquist: bool = False,
) -> SpectralSignalConfig:
    if not math.isfinite(float(coefficient_scale)) or float(coefficient_scale) <= 0.0:
        raise ValueError("coefficient_scale must be a finite float greater than 0.")
    if not math.isfinite(float(noise_floor_std)) or float(noise_floor_std) < 0.0:
        raise ValueError("noise_floor_std must be a finite float greater than or equal to 0.")
    if isinstance(nonzero_terms, bool) or not isinstance(nonzero_terms, int) or nonzero_terms < 0:
        raise ValueError("nonzero_terms must be a non-negative integer.")

    rfft_max_bin = samples_per_cycle // 2
    bin_floor = int(min_bin)
    bin_ceiling = rfft_max_bin if max_bin is None else int(max_bin)
    if bin_floor < 0 or bin_ceiling < 0 or bin_floor > bin_ceiling:
        raise ValueError("min_bin and max_bin must define a valid non-negative interval.")

    allowed_bins = [
        bin_index
        for bin_index in range(bin_floor, bin_ceiling + 1)
        if (allow_dc or bin_index != 0)
        and (allow_nyquist or samples_per_cycle % 2 != 0 or bin_index != rfft_max_bin)
    ]
    if nonzero_terms > len(allowed_bins):
        raise ValueError(
            f"Requested {nonzero_terms} terms but only {len(allowed_bins)} bins are available."
        )

    rng = np.random.default_rng(seed)
    selected_bins = np.sort(
        rng.choice(np.asarray(allowed_bins, dtype=np.int64), size=nonzero_terms, replace=False)
    )
    spectrum = np.zeros(rfft_max_bin + 1, dtype=np.complex128)
    coeff_scale = float(coefficient_scale)

    for bin_index in selected_bins:
        real = float(rng.normal(scale=coeff_scale))
        imag = float(rng.normal(scale=coeff_scale))
        if bin_index == 0:
            imag = 0.0
        if samples_per_cycle % 2 == 0 and bin_index == rfft_max_bin:
            imag = 0.0
        spectrum[int(bin_index)] = complex(real, imag)

    if noise_floor_std > 0.0:
        combined_cycle = np.fft.irfft(spectrum, n=samples_per_cycle)
        combined_cycle = combined_cycle + rng.normal(
            loc=0.0,
            scale=float(noise_floor_std),
            size=samples_per_cycle,
        )
        spectrum = np.fft.rfft(combined_cycle, n=samples_per_cycle)
        disallowed_bins = {
            bin_index
            for bin_index in range(rfft_max_bin + 1)
            if bin_index not in allowed_bins
        }
        for bin_index in disallowed_bins:
            spectrum[bin_index] = 0.0
        if not allow_dc:
            spectrum[0] = complex(0.0, 0.0)
        if not allow_nyquist and samples_per_cycle % 2 == 0:
            spectrum[rfft_max_bin] = complex(0.0, 0.0)

    terms = _terms_from_dense_spectrum(spectrum, samples_per_cycle=samples_per_cycle)
    return SpectralSignalConfig(
        signal_id=signal_id,
        sample_rate_hz=float(sample_rate_hz),
        samples_per_cycle=samples_per_cycle,
        terms=terms,
        offset=float(offset),
        scale=float(scale),
        output_dtype=_SUPPORTED_OUTPUT_DTYPE,
    )


def random_sparse_spectrum_generator(**kwargs: Any) -> SpectralSignalGenerator:
    return random_sparse_spectrum_config(**kwargs).build_generator()


def random_orientation_matrix_generator(
    *,
    sample_rate_hz: float,
    samples_per_cycle: int,
    seed: int,
    angle_scale_radians: float = 0.35,
) -> RotationMatrixSignalGenerator:
    if not math.isfinite(float(angle_scale_radians)) or float(angle_scale_radians) <= 0.0:
        raise ValueError("angle_scale_radians must be a finite float greater than 0.")
    rfft_max_bin = samples_per_cycle // 2
    if rfft_max_bin <= 0:
        spectrum_window = {
            "nonzero_terms": 1,
            "min_bin": 0,
            "max_bin": 0,
            "allow_dc": True,
        }
    else:
        spectrum_window = {
            "nonzero_terms": min(4, rfft_max_bin),
            "min_bin": 1,
            "max_bin": min(32, rfft_max_bin),
        }
    common = {
        "sample_rate_hz": float(sample_rate_hz),
        "samples_per_cycle": int(samples_per_cycle),
        "coefficient_scale": float(angle_scale_radians),
        "offset": 0.0,
        "scale": 1.0,
        "noise_floor_std": 0.0,
        **spectrum_window,
    }
    return RotationMatrixSignalGenerator(
        roll=random_sparse_spectrum_generator(signal_id="roll", seed=seed + 1, **common),
        pitch=random_sparse_spectrum_generator(signal_id="pitch", seed=seed + 2, **common),
        yaw=random_sparse_spectrum_generator(signal_id="yaw", seed=seed + 3, **common),
    )


def reconstruct_spectral_signal(path: str | Path) -> SpectralSignalConfig:
    return SpectralSignalConfig.reconstruct(path)


def _terms_from_dense_spectrum(
    spectrum: np.ndarray,
    *,
    samples_per_cycle: int,
) -> tuple[SpectralTerm, ...]:
    flat = np.asarray(spectrum, dtype=np.complex128).reshape(-1)
    terms: list[SpectralTerm] = []
    nyquist_bin = samples_per_cycle // 2 if samples_per_cycle % 2 == 0 else None

    for bin_index, coefficient in enumerate(flat):
        real = float(np.real(coefficient))
        imag = float(np.imag(coefficient))
        if abs(real) <= _SPECTRUM_ZERO_TOLERANCE:
            real = 0.0
        if abs(imag) <= _SPECTRUM_ZERO_TOLERANCE:
            imag = 0.0
        if bin_index == 0 or (nyquist_bin is not None and bin_index == nyquist_bin):
            imag = 0.0
        if real == 0.0 and imag == 0.0:
            continue
        terms.append(SpectralTerm(bin_index=bin_index, real=real, imag=imag))
    return tuple(terms)


def _rotation_matrix_from_euler_xyz(*, roll: float, pitch: float, yaw: float) -> np.ndarray:
    cx = math.cos(roll)
    sx = math.sin(roll)
    cy = math.cos(pitch)
    sy = math.sin(pitch)
    cz = math.cos(yaw)
    sz = math.sin(yaw)

    rx = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, cx, -sx],
            [0.0, sx, cx],
        ],
        dtype=np.float64,
    )
    ry = np.array(
        [
            [cy, 0.0, sy],
            [0.0, 1.0, 0.0],
            [-sy, 0.0, cy],
        ],
        dtype=np.float64,
    )
    rz = np.array(
        [
            [cz, -sz, 0.0],
            [sz, cz, 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    return rz @ ry @ rx
