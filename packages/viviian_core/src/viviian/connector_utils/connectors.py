from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Integral
import threading
import time
from typing import Any, Self

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight


_DEFAULT_HOST = "127.0.0.1"
_RETRY_DELAY_SECONDS = 0.05
_SERVER_START_DELAY_SECONDS = 0.05


@dataclass(slots=True)
class StreamSpec:
    stream_id: str
    schema: pa.Schema
    shape: tuple[int, int]
    stream: Any | None = None
    row_count: int = field(init=False)
    column_count: int = field(init=False)
    transport_schema: pa.Schema = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.stream_id:
            raise ValueError("stream_id must be non-empty.")
        if not isinstance(self.schema, pa.Schema):
            raise TypeError("schema must be a pyarrow.Schema.")
        if len(self.schema) < 1:
            raise ValueError("schema must define at least one field.")

        self.shape = _normalize_shape(self.shape)
        self.row_count, self.column_count = self.shape
        if self.column_count != len(self.schema):
            raise ValueError(
                f"shape {self.shape} does not match schema field count "
                f"{len(self.schema)}."
            )
        self.transport_schema = pa.schema(
            [(field.name, pa.float64()) for field in self.schema]
        )

    def normalize_batch(self, batch: np.ndarray) -> np.ndarray:
        if not isinstance(batch, np.ndarray):
            raise TypeError("batch must be a numpy.ndarray.")
        if batch.ndim != 2:
            raise ValueError("batch must be a 2D numpy.ndarray.")
        if tuple(batch.shape) != self.shape:
            raise ValueError(
                f"batch shape mismatch for stream {self.stream_id!r}: "
                f"expected {self.shape}, got {tuple(batch.shape)}."
            )
        if not np.issubdtype(batch.dtype, np.number):
            raise TypeError("batch dtype must be numeric.")

        batch = np.asarray(batch, dtype=np.float64)
        if not batch.flags.c_contiguous:
            batch = np.ascontiguousarray(batch)
        return batch



class SendConnector(flight.FlightServerBase):
    def __init__(
        self,
        stream_spec: StreamSpec,
        port: int,
        host: str = _DEFAULT_HOST,
    ) -> None:
        if not isinstance(stream_spec, StreamSpec):
            raise TypeError("stream_spec must be a StreamSpec.")
        if not host:
            raise ValueError("host must be non-empty.")
        if int(port) < 0:
            raise ValueError("port must be >= 0.")

        self.stream_spec = stream_spec
        self.host = str(host)
        self.batch = np.empty(stream_spec.shape, dtype=np.float64)
        self.has_batch = False
        self._version = 0
        self._condition = threading.Condition()
        self._closing = False
        self._closed = False
        self._server_thread: threading.Thread | None = None
        super().__init__(location=f"grpc://{self.host}:{int(port)}")

    @property
    def endpoint_uri(self) -> str:
        return f"grpc://{self.host}:{self.port}"

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self.close()
        return False

    def open(self) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("SendConnector is closed.")
            if self._server_thread is not None:
                return
            self._server_thread = threading.Thread(target=self.serve, daemon=True)
            self._server_thread.start()
        time.sleep(_SERVER_START_DELAY_SECONDS)

    def close(self) -> None:
        server_thread: threading.Thread | None
        with self._condition:
            if self._closed:
                return
            self._closing = True
            server_thread = self._server_thread
            self._condition.notify_all()

        self.shutdown()
        if server_thread is not None:
            server_thread.join(timeout=1.0)

        with self._condition:
            self._server_thread = None
            self._closed = True

    def send_numpy(self, batch: np.ndarray) -> None:
        batch = self.stream_spec.normalize_batch(batch)
        self.open()
        with self._condition:
            if self._closing or self._closed:
                raise RuntimeError("SendConnector is closed.")
            np.copyto(self.batch, batch)
            self.has_batch = True
            self._version += 1
            self._condition.notify_all()

    def do_get(
        self,
        _context: flight.ServerCallContext,
        ticket: flight.Ticket,
    ) -> flight.FlightDataStream:
        if ticket.ticket != self.stream_spec.stream_id.encode("utf-8"):
            raise ValueError(
                f"ticket mismatch for stream {self.stream_spec.stream_id!r}."
            )
        return flight.GeneratorStream(
            self.stream_spec.transport_schema,
            self._batch_stream(),
        )

    def _batch_stream(self):
        local_batch = np.empty(self.stream_spec.shape, dtype=np.float64)
        last_version = -1

        while True:
            with self._condition:
                while not self._closing and (
                    not self.has_batch or self._version == last_version
                ):
                    self._condition.wait()
                if self._closing:
                    return

                np.copyto(local_batch, self.batch)
                last_version = self._version

            yield self._batch_to_record_batch(local_batch)

    def _batch_to_record_batch(self, batch: np.ndarray) -> pa.RecordBatch:
        arrays = [
            pa.array(batch[:, index], type=pa.float64())
            for index in range(self.stream_spec.column_count)
        ]
        return pa.record_batch(arrays, schema=self.stream_spec.transport_schema)


class ReceiveConnector(flight.FlightClient):
    def __init__(
        self,
        stream_spec: StreamSpec,
        port: int,
        host: str = _DEFAULT_HOST,
    ) -> None:
        if not isinstance(stream_spec, StreamSpec):
            raise TypeError("stream_spec must be a StreamSpec.")
        if not host:
            raise ValueError("host must be non-empty.")
        if int(port) < 0:
            raise ValueError("port must be >= 0.")

        self.stream_spec = stream_spec
        self.host = str(host)
        self.connect_port = int(port)
        self.batch = np.empty(stream_spec.shape, dtype=np.float64)
        self.has_batch = False
        self._reader_thread: threading.Thread | None = None
        self._reader: flight.FlightStreamReader | None = None
        self._closing = False
        self._closed = False
        self.stream = stream_spec.stream
        super().__init__(f"grpc://{self.host}:{self.connect_port}")

    @property
    def endpoint_uri(self) -> str:
        return f"grpc://{self.host}:{self.connect_port}"

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        del exc_type, exc, tb
        self.close()
        return False

    def open(self) -> None:
        if self._closed:
            raise RuntimeError("ReceiveConnector is closed.")
        if self._reader_thread is not None:
            return
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        reader = self._reader
        if reader is not None:
            reader.cancel()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
        super().close()
        self._reader_thread = None
        self._closed = True

    def _reader_loop(self) -> None:
        connection_alive = False

        def write_disconnect_batch() -> None:
            nonlocal connection_alive
            if not connection_alive:
                return
            if self.stream is not None:
                self.stream.write(
                    np.full(self.stream_spec.shape, np.nan, dtype=np.float64)
                )
            connection_alive = False

        while not self._closing:
            try:
                self._reader = self.do_get(
                    flight.Ticket(self.stream_spec.stream_id.encode("utf-8"))
                )
                connection_alive = True
                while not self._closing:
                    chunk = self._reader.read_chunk()
                    record_batch = chunk.data
                    for index in range(self.stream_spec.column_count):
                        self.batch[:, index] = record_batch.column(index).to_numpy(
                            zero_copy_only=False
                        )
                    self.has_batch = True
                    if self.stream is not None:
                        self.stream.write(self.batch.copy())
            except StopIteration:
                write_disconnect_batch()
            except pa.ArrowException:
                write_disconnect_batch()
                if self._closing:
                    return
            finally:
                self._reader = None

            if not self._closing:
                time.sleep(_RETRY_DELAY_SECONDS)

        write_disconnect_batch()


__all__ = [
    "ReceiveConnector",
    "SendConnector",
    "StreamSpec",
]


def _normalize_shape(shape: tuple[int, int]) -> tuple[int, int]:
    try:
        shape_tuple = tuple(shape)
    except TypeError as exc:
        raise TypeError("shape must be an iterable of positive integers.") from exc
    if len(shape_tuple) != 2:
        raise ValueError("shape must be a 2D (rows, columns) tuple.")

    normalized: list[int] = []
    for dim in shape_tuple:
        if isinstance(dim, bool) or not isinstance(dim, Integral):
            raise TypeError("shape dimensions must be integers.")
        if int(dim) < 1:
            raise ValueError("shape dimensions must be >= 1.")
        normalized.append(int(dim))
    return normalized[0], normalized[1]
