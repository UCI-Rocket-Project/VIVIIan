from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sys
from typing import Iterable
import pandas as pd
import pyarrow as pa
import psycopg
import threading
from datetime import datetime, timedelta, timezone
import time




ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared_config import LiveTomlConfig


@dataclass
class QuestDBConnectionConfig:
    server: str
    pg_port: int
    user: str
    password: str
    dbname: str
    connect_timeout_seconds: float
    autocommit: bool


class QuestDBReader:
    def __init__(self, toml_path: str = "gse2_0.toml") -> None:
        self._toml_loader = LiveTomlConfig(toml_path)
        self._cfg = self._load_database_config()
        self._conn = None

    @staticmethod
    def _require(mapping: dict, key: str):
        if key not in mapping or mapping[key] is None:
            raise KeyError(f"Missing required config key: {key}")
        return mapping[key]

    def _load_database_config(self) -> QuestDBConnectionConfig:
        self._toml_loader.load_config()
        db = self._toml_loader.return_loaded_config("database")
        return QuestDBConnectionConfig(
            server=str(self._require(db, "server")),
            pg_port=int(self._require(db, "pg_port")),
            user=str(self._require(db, "user")),
            password=str(self._require(db, "password")),
            dbname=str(self._require(db, "dbname")),
            connect_timeout_seconds=float(self._require(db, "connect_timeout_seconds")),
            autocommit=bool(self._require(db, "autocommit")),
        )

    def reload_config(self) -> None:
        self._cfg = self._load_database_config()
        self.close()

    def connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(
                host=self._cfg.server,
                port=self._cfg.pg_port,
                user=self._cfg.user,
                password=self._cfg.password,
                dbname=self._cfg.dbname,
                connect_timeout=max(1, int(round(self._cfg.connect_timeout_seconds))),
                autocommit=self._cfg.autocommit,
            )
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()

    @staticmethod
    def _quote_ident(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def fetch_time_range(
        self,
        schema: str,
        table: str,
        timestamp_column: str,
        value_columns: str | list[str],
        start_time: datetime,
        end_time: datetime,
    ) -> tuple[list[str], list[tuple]]:
        if isinstance(value_columns, str):
            value_columns = [value_columns]
        if not value_columns:
            raise ValueError("value_columns must contain at least one column")

        quoted_ts = self._quote_ident(timestamp_column)
        quoted_values = [self._quote_ident(c) for c in value_columns]
        select_cols = ", ".join([quoted_ts] + quoted_values)
        sql = (
            f"SELECT {select_cols} "
            f"FROM {self._quote_ident(schema)}.{self._quote_ident(table)} "
            f"WHERE {quoted_ts} >= %s "
            f"AND {quoted_ts} <= %s "
            f"ORDER BY {quoted_ts}"
        )
        with self.connect().cursor() as cur:
            cur.execute(sql, (start_time, end_time))
            rows = cur.fetchall()
            columns = [d.name for d in cur.description]
        return columns, rows

    def build_grouped_table_specs(self, layout_name: str = "default") -> list[dict]:
        self._toml_loader.load_config()
        query_model = self._toml_loader.return_loaded_config("query_model")
        layout = self._require(query_model, layout_name)
        streams = self._require(layout, "streams")
        if not isinstance(streams, list) or not streams:
            raise ValueError(f"query_model.{layout_name}.streams must be a non-empty list")

        grouped: dict[tuple[str, str, str], dict] = {}
        for stream in streams:
            schema = str(self._require(stream, "schema"))
            table = str(self._require(stream, "table"))
            ts_col = str(self._require(stream, "timestamp_column"))
            value_col = str(self._require(stream, "value_column"))
            key = (schema, table, ts_col)
            if key not in grouped:
                grouped[key] = {
                    "name": f"{schema}.{table}",
                    "schema": schema,
                    "table": table,
                    "timestamp_column": ts_col,
                    "value_columns": [],
                }
            if value_col not in grouped[key]["value_columns"]:
                grouped[key]["value_columns"].append(value_col)

        return list(grouped.values())

    @staticmethod
    def rows_to_arrow(columns: list[str], rows: Iterable[tuple]) -> pa.Table:
        rows = list(rows)
        if not rows:
            return pa.table({c: pa.array([]) for c in columns})
        col_data = list(zip(*rows))
        return pa.table({col: pa.array(values) for col, values in zip(columns, col_data)})

    @staticmethod
    def rows_to_pandas(columns: list[str], rows: Iterable[tuple]) -> pd.DataFrame:
        return QuestDBReader.rows_to_arrow(columns, rows).to_pandas()
    

def test_loop(buffer_lock, shared_snapshot): 
    while True: 
        with buffer_lock: 
            snapshot_columns = shared_snapshot["columns"]
            snapshot_rows = shared_snapshot["rows"]
        arrow_table = QuestDBReader.rows_to_arrow(snapshot_columns, snapshot_rows)
        latest = snapshot_rows[-1] if snapshot_rows else None
        print(f"rows={arrow_table.num_rows} latest={latest}")
        time.sleep(0.1)


def reader_loop(reader: QuestDBReader, buffer_lock, shared_snapshot, stop_event, window_seconds: int = 10, poll_seconds: float = 0.1):
    while not stop_event.is_set():
        try:
            specs = reader.build_grouped_table_specs("default")
            if specs:
                spec = specs[0]  # simple tester: first grouped table spec
                end_time = datetime.now(timezone.utc)
                start_time = end_time - timedelta(seconds=window_seconds)
                fetched_columns, fetched_rows = reader.fetch_time_range(
                    schema=spec["schema"],
                    table=spec["table"],
                    timestamp_column=spec["timestamp_column"],
                    value_columns=spec["value_columns"],
                    start_time=start_time,
                    end_time=end_time,
                )
                # Fast pointer switch: keep lock held for the minimum possible time.
                with buffer_lock:
                    shared_snapshot["columns"] = fetched_columns
                    shared_snapshot["rows"] = fetched_rows
        except Exception as e:
            print(f"reader_loop error: {e}")
        time.sleep(poll_seconds)
    

if __name__ == "__main__":
    test_reader = QuestDBReader("../gse2_0.toml")

    buffer_lock = threading.Lock()
    shared_snapshot = {"columns": ["timestamp"], "rows": []}
    stop_event = threading.Event()
    reader_thread = threading.Thread(
        target=reader_loop,
        args=(test_reader, buffer_lock, shared_snapshot, stop_event),
        daemon=True,
    )
    reader_thread.start()

    try:
        test_loop(buffer_lock, shared_snapshot)
    except KeyboardInterrupt:
        stop_event.set()
        reader_thread.join(timeout=1.0)
        test_reader.close()




    
