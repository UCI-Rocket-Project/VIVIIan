from dataclasses import dataclass
import tomllib
import logging


@dataclass
class NidaqConfig:
    device: str
    channel_sampling_rate: int
    buffer_duration_sec: int
    polling_freq: int


@dataclass
class DatabaseConfig:
    server: str
    http_port: int
    questdb_table: str
    
    @property
    def questdb_conf(self) -> str:
        return f"http::addr={self.server}:{self.http_port};"


@dataclass
class NidaqStreamConfig:
    host: str
    port: int
    raw_batch_points: int
    plot_points: int
    default_window_s: float
    fft_top_n: int


@dataclass
class SignalConfig:
    name: str
    source_column: str
    avg_n: int


@dataclass
class GraphCellConfig:
    title: str
    signals: list[str]
    window_s: float


def load_toml_config(path: str = "gse2_0.toml") -> tuple[NidaqConfig, DatabaseConfig, NidaqStreamConfig, dict[str, SignalConfig], list[GraphCellConfig]]:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

    # Nidaq Config
    nq = cfg.get("nidaq", {})
    nidaq_cfg = NidaqConfig(
        device=str(nq.get("device", "Dev1")),
        channel_sampling_rate=int(nq.get("channel_sampling_rate", 50000)),
        buffer_duration_sec=int(nq.get("buffer_duration_sec", 20)),
        polling_freq=int(nq.get("polling_freq", 1))
    )

    # Database Config
    db = cfg.get("database", {})
    # Get table from first stream in query_model.default.streams if available
    qdb_table = "LOAD_CELL"
    try:
        qdb_table = cfg["query_model"]["default"]["streams"][0]["table"]
    except KeyError:
        logging.warning("No table found in query_model.default.streams, defaulting to LOAD_CELL")
        pass

    db_cfg = DatabaseConfig(
        server=str(db.get("server", "localhost")),
        http_port=int(db.get("http_port", 9000)),
        questdb_table=str(qdb_table)
    )

    ns = cfg.get("nidaq_stream", {})
    stream_cfg = NidaqStreamConfig(
        host=str(ns.get("host", "127.0.0.1")),
        port=int(ns.get("port", 50100)),
        raw_batch_points=int(ns.get("raw_batch_points", 200)),
        plot_points=int(ns.get("plot_points", 1200)),
        default_window_s=float(ns.get("default_window_s", 300.0)),
        fft_top_n=int(ns.get("fft_top_n", 8)),
    )

    signals: dict[str, SignalConfig] = {}
    for s in cfg.get("nidaq_signals", []):
        sc = SignalConfig(
            name=str(s["name"]),
            source_column=str(s["source_column"]),
            avg_n=max(1, int(s.get("avg_n", 50))),
        )
        signals[sc.name] = sc

    cells: list[GraphCellConfig] = []
    for c in cfg.get("graph_cells", []):
        cells.append(
            GraphCellConfig(
                title=str(c.get("title", "Graph Cell")),
                signals=[str(x) for x in c.get("signals", [])],
                window_s=float(c.get("window_s", stream_cfg.default_window_s)),
            )
        )
    if not cells and signals:
        cells.append(GraphCellConfig(title="Signals", signals=list(signals.keys()), window_s=stream_cfg.default_window_s))

    return nidaq_cfg, db_cfg, stream_cfg, signals, cells
