import logging
import socket
import threading
import time

import pyarrow as pa
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backend")

class ArrowBatchStreamServer:
    """Minimal TCP server that broadcasts Arrow IPC frames to one client."""

    def __init__(self, host: str, port: int):
        self._data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._data_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._data_sock.bind((host, port))
        self._data_sock.listen(1)
        self._data_sock.setblocking(False)

        self._metadata_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._metadata_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._metadata_sock.bind((host, port + 1))
        self._metadata_sock.listen(1)
        self._metadata_sock.setblocking(False)

        self._data_client = None
        self._metadata_client = None
        logging.info("Arrow stream server listening on %s:%s", host, port)
        logging.info("Metadata server listening on %s:%s", host, port + 1)

    def _try_accept_data(self) -> None:
        if self._data_client is not None:
            return
        try:
            client, addr = self._data_sock.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._data_client = client
            logging.info("Arrow stream client connected: %s", addr)
        except BlockingIOError:
            return

    def _try_accept_metadata(self) -> None:
        if self._metadata_client is not None:
            return
        try:
            client, addr = self._metadata_sock.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._metadata_client = client
            logging.info("Metadata client connected: %s", addr)
        except BlockingIOError:
            return

    def send_table(self, table: pa.Table) -> None:
        logger.debug("server.send_table: try_accept/start rows=%d", table.num_rows)
        self._try_accept_data()
        if self._data_client is None:
            logger.debug("server.send_table: no client connected, skip")
            return
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        payload = sink.getvalue().to_pybytes()
        frame = len(payload).to_bytes(4, "big") + payload
        try:
            self._data_client.sendall(frame)
            logger.debug("server.send_table: sent bytes=%d rows=%d", len(frame), table.num_rows)
        except OSError:
            try:
                self._data_client.close()
            except OSError:
                pass
            self._data_client = None

    def close(self) -> None:
        if self._data_client is not None:
            self._data_client.close()
            self._data_client = None
        self._data_sock.close()

def stream_chunks(data, n):
    for i in range(0, len(data), n):
        yield data[i:i + n]

class VIVIIan:
    def __init__(self, schema: pa.Schema, tx_timeout: float = 1, max_rows: int = 1000, frontend_ip: str = "127.0.0.1", frontend_port: int = 6767):
        # Configuration
        self.tx_timeout = tx_timeout
        self.max_rows = max_rows
        
        # Internal State
        self._schema = None
        self.table_list: list[pa.Table] = []
        self.stats = {"sent_batches": 0, "sent_rows": 0, "drops": 0, "queued_rows": 0}
        
        # Concurrency & Network resources
        self.stop_event = threading.Event()
        self.stream_server = ArrowBatchStreamServer(frontend_ip, frontend_port)
        self._sender_thread = None

        self._define_schema(schema)

    def ingress_table(self, table: pa.Table) -> None:
        """Accepts a table, verifies schema, and queues it for sending."""
        if self._schema is not None:
            try:
                table = table.cast(self._schema)
            except pa.ArrowInvalid as e:
                logger.error(f"Schema mismatch during ingress: {e}")
                return
        else:
            raise RuntimeError("VIVIIan API schema never initialized")

        table_rows = table.num_rows
        self.stats["queued_rows"] += table_rows
        self.table_list.append(table)
        
        logger.debug("backend_ingress: added rows=%d total queued rows=%d qsize=%d", 
                     table_rows, self.stats["queued_rows"], len(self.table_list))
    
    def _define_schema(self, schema: pa.Schema) -> None:
        """Sets the expected schema for incoming tables. Note that some field that's defined as a timestamp should exist."""
        has_timestamp = False
        for name in schema.names:
            fieldType = schema.field(name).type
            if fieldType == pa.time64('ns'):
                has_timestamp = True

        if not has_timestamp:
            raise ValueError("Invalid schema (no nanosecond compatible timestamp)")
            
        self._schema = schema

    def _tx_table(self):
        for chunk in stream_chunks(self.table_list, self.max_rows):
            concat_tables = pa.concat_tables(chunk) 
            self.stream_server.send_table(concat_tables)
            
            self.stats["sent_batches"] += 1
            self.stats["sent_rows"] += concat_tables.num_rows
        
        # Reset state
        self.table_list.clear()
        self.stats["queued_rows"] = 0  

    def _sender_loop(self) -> None:
        """Background thread loop that batches and sends data."""
        logger.info("backend_loop: started")
        last_time = time.monotonic()
        
        while not self.stop_event.is_set():
            time_elapsed = time.monotonic() - last_time 
            
            has_timedout = self.stats["queued_rows"] > 0 and time_elapsed > self.tx_timeout
            has_maxrows = self.stats["queued_rows"] >= max(1, int(self.max_rows))

            if has_timedout or has_maxrows:
                self._tx_table()
                last_time = time.monotonic() 

            time.sleep(0.001)

    def __enter__(self):
        self._sender_thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._sender_thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logger.info("backend: shutting down connection")
        self._tx_table()
        self.stop_event.set()
        self.stream_server.close()
        
        if self._sender_thread is not None:
            self._sender_thread.join(timeout=2.0)