"""Plotly figure builders. Each module emits a JSON-serializable spec
that the browser renders via Plotly.newPlot()."""

from .nlv import build_nlv_figure
from .coverage import build_coverage_figure
from .expiration import build_expiration_ladder_figure
from .parkev_timeline import build_parkev_timeline
from .sparkline import build_ticker_sparkline
from .benchmark import build_benchmark_figure

__all__ = [
    "build_nlv_figure",
    "build_coverage_figure",
    "build_expiration_ladder_figure",
    "build_parkev_timeline",
    "build_ticker_sparkline",
    "build_benchmark_figure",
]
