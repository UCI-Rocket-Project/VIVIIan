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
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(1)
        self._sock.setblocking(False)
        self._client = None
        logging.info("Arrow stream server listening on %s:%s", host, port)

    def _try_accept(self) -> None:
        if self._client is not None:
            return
        try:
            client, addr = self._sock.accept()
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._client = client
            logging.info("Arrow stream client connected: %s", addr)
        except BlockingIOError:
            return

    def send_table(self, table: pa.Table) -> None:
        logger.debug("server.send_table: try_accept/start rows=%d", table.num_rows)
        self._try_accept()
        if self._client is None:
            logger.debug("server.send_table: no client connected, skip")
            return
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        payload = sink.getvalue().to_pybytes()
        frame = len(payload).to_bytes(4, "big") + payload
        try:
            self._client.sendall(frame)
            logger.debug("server.send_table: sent bytes=%d rows=%d", len(frame), table.num_rows)
        except OSError:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._sock.close()

def stream_chunks(data, n):
    for i in range(0, len(data), n):
        yield data[i:i + n]

class VIVIIanAPI():
    def __init__(self, ingress_table):
        self.ingress_table = ingress_table

@contextmanager
def VIVIIan(tx_timeout: float = 1, max_rows: int = 1000, frontend_ip: str = "127.0.0.1", frontend_port: int = 6767):
    stop_event = threading.Event()

    stream_server = ArrowBatchStreamServer(frontend_ip, frontend_port)

    table_list: list[pa.Table] = []
    stats = {"sent_batches": 0, "sent_rows": 0, "drops": 0, "queued_rows": 0}

    def ingress_table(table: pa.Table):
        table_rows = table.num_rows
        stats["queued_rows"] += table_rows
        table_list.append(table)
        logger.debug("backend_ingress: added rows=%d total queued rows=%d qsize=%d", table_rows, stats["queued_rows"], len(table_list))

    def sender_loop() -> None:
        logger.info("backend_loop: started")

        last_time = time.monotonic()
        while not stop_event.is_set():
            time_elapsed = time.monotonic() - last_time 

            has_timedout = stats["queued_rows"] > 0 and time_elapsed > tx_timeout
            has_maxrows = stats["queued_rows"] >= max(1, int(max_rows))

            if has_timedout or has_maxrows:
                for chunk in stream_chunks(table_list, max_rows):
                    concat_tables = pa.concat_arrays(chunk)
                    stream_server.send_table(concat_tables)
                    stats["sent_batches"] += 1
                    stats["sent_rows"] += concat_tables.num_rows
                table_list.clear()

            time.sleep(0.001) # Better timeframe

    t = threading.Thread(target=sender_loop, daemon=True)
    t.start()

    try:
        yield VIVIIanAPI(ingress_table=ingress_table)
    finally:
        logging.info("backend: shutting down connection")
        stop_event.set()
        stream_server.close()