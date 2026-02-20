from dataclasses import dataclass
import tomllib


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


def load_tui_config(path: str = "gse2_0.toml") -> tuple[NidaqStreamConfig, dict[str, SignalConfig], list[GraphCellConfig]]:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)

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

    return stream_cfg, signals, cells
