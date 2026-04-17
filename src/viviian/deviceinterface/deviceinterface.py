from __future__ import annotations

import logging
import threading
import time
from typing import Iterator

import numpy as np
import pyarrow as pa

from viviian.connector_utils import SendConnector, StreamSpec

logger = logging.getLogger("deviceinterface")


def _build_stream_spec(schema: pa.Schema, max_rows: int, stream_id: str) -> StreamSpec:
    float_schema = pa.schema([(f.name, pa.float64()) for f in schema])
    return StreamSpec(
        stream_id=stream_id,
        schema=float_schema,
        shape=(max(1, int(max_rows)), len(schema)),
    )


class DeviceInterface:
    def __init__(
        self,
        schema: pa.Schema,
        tx_timeout: float = 1,
        max_rows: int = 1000,
        publish_host: str = "127.0.0.1",
        publish_port: int = 6767,
        stream_id: str = "device",
        sender: SendConnector | None = None,
    ) -> None:
        self.tx_timeout = tx_timeout
        self._max_rows = max(1, int(max_rows))

        self._schema: pa.Schema | None = None
        self.table_list: list[pa.Table] = []
        self.stats = {"sent_batches": 0, "sent_rows": 0, "drops": 0, "queued_rows": 0}
        self._lock = threading.Lock()

        self.stop_event = threading.Event()
        self._sender_thread: threading.Thread | None = None

        self._define_schema(schema)

        if sender is not None:
            self._sender = sender
        else:
            spec = _build_stream_spec(schema, self._max_rows, stream_id)
            self._sender = SendConnector(spec, publish_port, host=publish_host)

    def ingress_table(self, table: pa.Table) -> None:
        """Accepts a table, verifies schema, and queues it for sending."""
        if self._schema is not None:
            try:
                table = table.cast(self._schema)
            except pa.ArrowInvalid as e:
                logger.error(f"Schema mismatch during ingress: {e}")
                return
        else:
            raise RuntimeError("DeviceInterface schema never initialized")

        table_rows = table.num_rows
        with self._lock:
            self.stats["queued_rows"] += table_rows
            self.table_list.append(table)

        logger.debug(
            "deviceinterface_ingress: added rows=%d total queued rows=%d qsize=%d",
            table_rows,
            self.stats["queued_rows"],
            len(self.table_list),
        )

    def _define_schema(self, schema: pa.Schema) -> None:
        """Sets the expected schema. Requires at least one pa.time64('ns') field."""
        has_timestamp = False
        for name in schema.names:
            if schema.field(name).type == pa.time64("ns"):
                has_timestamp = True

        if not has_timestamp:
            raise ValueError("Invalid schema (no nanosecond compatible timestamp)")

        self._schema = schema

    def _snapshot_pending_tables(self) -> list[pa.Table]:
        with self._lock:
            pending_tables = self.table_list
            self.table_list = []
            self.stats["queued_rows"] = 0
        return pending_tables

    def _chunk_table(self, table: pa.Table) -> Iterator[pa.Table]:
        for start in range(0, table.num_rows, self._max_rows):
            yield table.slice(start, self._max_rows)

    def _table_chunk_to_numpy(self, table: pa.Table) -> np.ndarray:
        assert self._schema is not None
        num_cols = len(self._schema)
        out = np.full((self._max_rows, num_cols), np.nan, dtype=np.float64)
        for col_idx, col_name in enumerate(self._schema.names):
            col = table.column(col_name)
            try:
                vals = col.cast(pa.float64()).to_numpy(zero_copy_only=False)
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                vals = col.cast(pa.int64()).to_numpy(zero_copy_only=False).astype(np.float64)
            out[: table.num_rows, col_idx] = vals
        return out

    def _tx_table(self) -> None:
        pending_tables = self._snapshot_pending_tables()
        if not pending_tables:
            return

        concat_table = pa.concat_tables(pending_tables)
        for chunk_table in self._chunk_table(concat_table):
            self._sender.send_numpy(self._table_chunk_to_numpy(chunk_table))
            with self._lock:
                self.stats["sent_batches"] += 1
                self.stats["sent_rows"] += chunk_table.num_rows

    def _sender_loop(self) -> None:
        """Background thread loop that batches and sends data."""
        logger.info("deviceinterface_loop: started")
        last_time = time.monotonic()

        while not self.stop_event.is_set():
            time_elapsed = time.monotonic() - last_time
            with self._lock:
                queued_rows = self.stats["queued_rows"]

            has_timedout = queued_rows > 0 and time_elapsed > self.tx_timeout
            has_maxrows = queued_rows >= self._max_rows

            if has_timedout or has_maxrows:
                self._tx_table()
                last_time = time.monotonic()

            time.sleep(0.001)

    def __enter__(self) -> DeviceInterface:
        self._sender.open()
        self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender_thread.start()
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        logger.info("deviceinterface: shutting down connection")
        self.stop_event.set()
        if self._sender_thread is not None:
            self._sender_thread.join(timeout=2.0)
        self._tx_table()
        self._sender.close()
