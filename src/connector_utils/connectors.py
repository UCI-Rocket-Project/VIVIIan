from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Integral
import threading
import time
from typing import ClassVar

import numpy as np
import pyarrow as pa
import pyarrow.flight as flight

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_TIMEOUT_SECONDS = 1.0
_SERVER_START_DELAY_SECONDS = 0.05


@dataclass(slots=True)
class StreamSpec:
    stream_id: str
    schema_version: int
    schema: pa.Schema
    shape: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if not self.stream_id:
            raise ValueError("stream_id must be non-empty.")
        if int(self.schema_version) < 1:
            raise ValueError("schema_version must be >= 1.")
        if not isinstance(self.schema, pa.Schema):
            raise TypeError("schema must be a pyarrow.Schema.")
        if len(self.schema) < 1:
            raise ValueError("schema must define at least one field.")

        self.schema_version = int(self.schema_version)
        if self.shape is None:
            self.shape = _default_shape_for_schema(self.schema)
        else:
            self.shape = _normalized_shape(self.shape)
        self._validate_shape_contract()

    def __repr__(self) -> str:
        return (
            "StreamSpec("
            f"stream_id={self.stream_id!r}, "
            f"schema_version={self.schema_version}, "
            f"shape={self.shape!r}, "
            f"fields={tuple(self.schema.names)!r})"
        )

    @property
    def descriptor(self) -> flight.FlightDescriptor:
        return flight.FlightDescriptor.for_path(
            self.stream_id,
            str(self.schema_version),
        )

    def validate_table(self, table: pa.Table) -> pa.Table:
        table = self._require_table(table)
        if not table.schema.equals(self.schema, check_metadata=True):
            raise ValueError(
                f"Schema mismatch for stream {self.stream_id!r}: "
                f"expected {self.schema}, got {table.schema}."
            )
        self._validate_table_shape(table)
        return table

    def validate_transport_table(self, table: pa.Table) -> pa.Table:
        table = self._require_table(table)
        if not table.schema.equals(self.transport_schema, check_metadata=True):
            raise ValueError(
                f"Transport schema mismatch for stream {self.stream_id!r}: "
                f"expected {self.transport_schema}, got {table.schema}."
            )
        self._validate_table_shape(table)
        return table

    def validate_descriptor(self, descriptor: flight.FlightDescriptor) -> None:
        actual = tuple(part.decode("utf-8") for part in descriptor.path)
        expected = (self.stream_id, str(self.schema_version))
        if actual != expected:
            raise ValueError(
                f"Descriptor mismatch for stream {self.stream_id!r}: "
                f"expected {expected!r}, got {actual!r}."
            )

    @property
    def transport_schema(self) -> pa.Schema:
        """All-float64 schema used on the wire."""
        return pa.schema([(f.name, pa.float64()) for f in self.schema])

    def to_transport(self, table: pa.Table) -> pa.Table:
        """Cast a validated table to all-float64 for wire transport."""
        table = self.validate_table(table)
        arrays = []
        for col in table.columns:
            if pa.types.is_temporal(col.type):
                col = col.cast(pa.int64())
            arrays.append(col.cast(pa.float64()))
        return pa.table(arrays, schema=self.transport_schema)

    def from_transport(self, table: pa.Table) -> pa.Table:
        """Cast a float64 transport table back to the original schema types."""
        table = self.validate_transport_table(table)
        arrays = []
        for i in range(table.num_columns):
            target = self.schema.field(i).type
            col = table.column(i)
            if pa.types.is_temporal(target):
                col = col.cast(pa.int64())
            arrays.append(col.cast(target))
        return pa.table(arrays, schema=self.schema)

    def numpy_to_transport(self, batch: np.ndarray) -> pa.Table:
        array = self._require_numpy_batch(batch)
        if array.ndim == 1:
            columns = [pa.array(array, type=pa.float64())]
        else:
            columns = [
                pa.array(array[index], type=pa.float64())
                for index in range(array.shape[0])
            ]
        return pa.table(columns, schema=self.transport_schema)

    def transport_to_numpy_batch(self, table: pa.Table) -> np.ndarray:
        table = self.validate_transport_table(table)
        columns = self._columns_to_numpy(table)
        if len(columns) == 1:
            batch = np.asarray(columns[0], dtype=np.float64)
        else:
            batch = np.stack(columns, axis=0).astype(np.float64, copy=False)
        if tuple(batch.shape) != self.shape:
            raise ValueError(
                f"Transport batch shape mismatch for stream {self.stream_id!r}: "
                f"expected {self.shape}, got {tuple(batch.shape)}."
            )
        return batch

    def _require_table(self, table: pa.Table) -> pa.Table:
        if not isinstance(table, pa.Table):
            raise TypeError("table must be a pyarrow.Table.")
        return table

    def _validate_shape_contract(self) -> None:
        field_count = len(self.schema)
        if len(self.shape) == 1:
            if field_count != 1:
                raise ValueError(
                    "1D stream shapes require exactly one schema field."
                )
            return

        if len(self.shape) != 2:
            raise ValueError(
                "shape must be (frames,) for single-field streams or "
                "(field_count, frames) for multi-field streams."
            )
        if field_count == 1:
            raise ValueError(
                "Single-field streams must use shape (frames,), not (1, frames)."
            )
        if self.shape[0] != field_count:
            raise ValueError(
                f"shape {self.shape} does not match schema field count {field_count}."
            )

    def _validate_table_shape(self, table: pa.Table) -> None:
        expected_rows = self.shape[0] if len(self.shape) == 1 else self.shape[1]
        if table.num_rows != expected_rows:
            raise ValueError(
                f"Row count mismatch for stream {self.stream_id!r}: "
                f"expected {expected_rows}, got {table.num_rows}."
            )
        expected_columns = 1 if len(self.shape) == 1 else self.shape[0]
        if table.num_columns != expected_columns:
            raise ValueError(
                f"Column count mismatch for stream {self.stream_id!r}: "
                f"expected {expected_columns}, got {table.num_columns}."
            )

    def _require_numpy_batch(self, batch: np.ndarray) -> np.ndarray:
        try:
            array = np.asarray(batch, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "batch must be convertible to a float64 numpy.ndarray."
            ) from exc
        if tuple(array.shape) != self.shape:
            raise ValueError(
                f"NumPy batch shape mismatch for stream {self.stream_id!r}: "
                f"expected {self.shape}, got {tuple(array.shape)}."
            )
        if not array.flags.c_contiguous:
            array = np.ascontiguousarray(array)
        return array

    def _columns_to_numpy(self, table: pa.Table) -> tuple[np.ndarray, ...]:
        return tuple(
            table.column(index).combine_chunks().to_numpy(zero_copy_only=False)
            for index in range(table.num_columns)
        )


@dataclass(slots=True, repr=False)
class DefaultConnector:
    DIRECTION: ClassVar[str] = "default"

    stream_spec: StreamSpec
    port: int
    host: str = _DEFAULT_HOST
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        if not isinstance(self.stream_spec, StreamSpec):
            raise TypeError("stream_spec must be a StreamSpec.")
        if not self.host:
            raise ValueError("host must be non-empty.")
        if int(self.port) < 0:
            raise ValueError("port must be >= 0.")
        if float(self.timeout_seconds) <= 0.0:
            raise ValueError("timeout_seconds must be > 0.")

        self.port = int(self.port)
        self.host = str(self.host)
        self.timeout_seconds = float(self.timeout_seconds)

    @property
    def direction(self) -> str:
        return self.DIRECTION

    @property
    def endpoint_uri(self) -> str:
        return f"grpc://{self.host}:{self.port}"

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"stream_id={self.stream_spec.stream_id!r}, "
            f"schema_version={self.stream_spec.schema_version}, "
            f"direction={self.direction!r}, "
            f"endpoint={self.endpoint_uri!r})"
        )


@dataclass(slots=True, repr=False)
class SendConnector(DefaultConnector):
    DIRECTION: ClassVar[str] = "send"

    _client: flight.FlightClient | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def open(self) -> None:
        if self._client is None:
            self._client = flight.FlightClient(self.endpoint_uri)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def send_table(self, table: pa.Table) -> None:
        transport = self.stream_spec.to_transport(table)
        self._send_transport_table(transport)

    def send_numpy(self, batch: np.ndarray) -> None:
        transport = self.stream_spec.numpy_to_transport(batch)
        self._send_transport_table(transport)

    def _send_transport_table(self, table: pa.Table) -> None:
        table = self.stream_spec.validate_transport_table(table)
        self.open()
        assert self._client is not None
        options = flight.FlightCallOptions(timeout=self.timeout_seconds)
        writer, _ = self._client.do_put(
            self.stream_spec.descriptor,
            table.schema,
            options=options,
        )
        try:
            writer.write_table(table)
        finally:
            writer.close()


@dataclass(slots=True, repr=False)
class ReceiveConnector(DefaultConnector):
    DIRECTION: ClassVar[str] = "receive"

    _latest_table: pa.Table | None = field(default=None, init=False, repr=False)
    _table_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _server: "_ConnectorFlightServer | None" = field(
        default=None,
        init=False,
        repr=False,
    )
    _server_thread: threading.Thread | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        DefaultConnector.__post_init__(self)

    def open(self) -> None:
        if self._server is not None:
            return
        self._server = _ConnectorFlightServer(self)
        self.port = int(self._server.port)
        self._server_thread = threading.Thread(target=self._server.serve, daemon=True)
        self._server_thread.start()
        time.sleep(_SERVER_START_DELAY_SECONDS)

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        if self._server_thread is not None:
            self._server_thread.join(timeout=max(1.0, self.timeout_seconds))
        self._server = None
        self._server_thread = None

    def recv_table(self) -> pa.Table | None:
        with self._table_lock:
            table = self._latest_table
            self._latest_table = None
            return table

    def recv_numpy(self) -> tuple[np.ndarray, ...] | None:
        table = self.recv_table()
        if table is None:
            return None

        return self.stream_spec._columns_to_numpy(table)

    def recv_typed_numpy(self) -> tuple[np.ndarray, ...] | None:
        """Like recv_numpy but casts float64 transport data back to original schema types."""
        table = self.recv_table()
        if table is None:
            return None
        typed = self.stream_spec.from_transport(table)
        return self.stream_spec._columns_to_numpy(typed)

    def recv_numpy_batch(self) -> np.ndarray | None:
        table = self.recv_table()
        if table is None:
            return None
        return self.stream_spec.transport_to_numpy_batch(table)

    def _accept_table(self, table: pa.Table) -> None:
        table = self.stream_spec.validate_transport_table(table)
        with self._table_lock:
            self._latest_table = table


Connector = DefaultConnector

__all__ = [
    "Connector",
    "DefaultConnector",
    "ReceiveConnector",
    "SendConnector",
    "StreamSpec",
]


class _ConnectorFlightServer(flight.FlightServerBase):
    def __init__(self, connector: ReceiveConnector) -> None:
        self._connector = connector
        super().__init__(location=connector.endpoint_uri)

    def do_put(
        self,
        context: flight.ServerCallContext,
        descriptor: flight.FlightDescriptor,
        reader: flight.MetadataRecordBatchReader,
        writer: flight.FlightMetadataWriter,
    ) -> None:
        del context, writer
        self._connector.stream_spec.validate_descriptor(descriptor)
        self._connector._accept_table(reader.read_all())


def _normalized_shape(shape: tuple[int, ...]) -> tuple[int, ...]:
    try:
        shape_tuple = tuple(shape)
    except TypeError as exc:
        raise TypeError("shape must be an iterable of positive integers.") from exc
    if not shape_tuple:
        raise ValueError("shape must be non-empty.")

    normalized: list[int] = []
    for dim in shape_tuple:
        if isinstance(dim, bool) or not isinstance(dim, Integral):
            raise TypeError("shape dimensions must be integers.")
        if int(dim) < 1:
            raise ValueError("shape dimensions must be >= 1.")
        normalized.append(int(dim))
    return tuple(normalized)


def _default_shape_for_schema(schema: pa.Schema) -> tuple[int, ...]:
    field_count = len(schema)
    if field_count == 1:
        return (1,)
    return (field_count, 1)
