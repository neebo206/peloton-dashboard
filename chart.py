"""Chart rendering for the Peloton post-workout dashboard."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ---------------------------------------------------------------------------
# Target band extraction
# ---------------------------------------------------------------------------

def _find_intervals(data: dict | list) -> list[dict] | None:
    if isinstance(data, list):
        return data if data else None

    for key in ("target_metrics", "data", "segments", "intervals"):
        candidate = data.get(key)
        if isinstance(candidate, list) and candidate:
            return candidate
        if isinstance(candidate, dict):
            nested = candidate.get("intervals") or candidate.get("segments")
            if nested:
                return nested
    return None


def build_target_band(
    target_data: dict | list | None,
    watt_fn: Callable[[float, float], float],
) -> pd.DataFrame | None:
    if not target_data:
        return None

    intervals = _find_intervals(target_data)
    if not intervals:
        return None

    records: list[dict] = []
    for seg in intervals:
        # Time offsets: new format uses {"offsets": {"start": N, "end": N}}
        offsets = seg.get("offsets")
        if offsets:
            start = int(offsets.get("start", 0))
            end = int(offsets.get("end", 0))
        else:
            start = int(seg.get("start_time_offset") or seg.get("start_offset") or 0)
            end = int(seg.get("end_time_offset") or seg.get("end_offset") or 0)
        if end <= start:
            continue

        # Cadence/resistance: new format uses {"metrics": [{"name": "resistance", "lower": N, "upper": N}, ...]}
        metrics_list = seg.get("metrics")
        if metrics_list:
            by_name = {m["name"]: m for m in metrics_list}
            res = by_name.get("resistance", {})
            cad = by_name.get("cadence", {})
            r_min = float(res.get("lower", 0))
            r_max = float(res.get("upper", 0))
            c_min = float(cad.get("lower", 0))
            c_max = float(cad.get("upper", 0))
        else:
            r_min = float(seg.get("resistance_start") or seg.get("hz_resistance_min") or seg.get("resistance_lower") or 0)
            r_max = float(seg.get("resistance_end") or seg.get("hz_resistance_max") or seg.get("resistance_upper") or 0)
            c_min = float(seg.get("cadence_start") or seg.get("hz_cadence_min") or seg.get("cadence_lower") or 0)
            c_max = float(seg.get("cadence_end") or seg.get("hz_cadence_max") or seg.get("cadence_upper") or 0)

        floor_w = watt_fn(c_min, r_min)
        ceiling_w = watt_fn(c_max, r_max)

        for sec in range(start, end + 1):
            records.append({
                "second": sec,
                "watt_floor": floor_w,
                "watt_ceiling": ceiling_w,
                "cadence_low": c_min,
                "cadence_high": c_max,
                "resistance_low": r_min,
                "resistance_high": r_max,
            })

    if not records:
        return None

    return pd.DataFrame(records).set_index("second")


# ---------------------------------------------------------------------------
# Performance graph helpers
# ---------------------------------------------------------------------------

def extract_metric(performance_data: dict, slug: str) -> list[float]:
    for m in performance_data.get("metrics", []):
        if m.get("slug") == slug:
            return [v or 0.0 for v in m.get("values", [])]
    for m in performance_data.get("averages", []):
        if m.get("slug") == slug:
            return [v or 0.0 for v in m.get("values", [])]
    return []


# ---------------------------------------------------------------------------
# Plotly chart
# ---------------------------------------------------------------------------

def plot_workout_plotly(
    meta: dict,
    performance_data: dict,
    target_data: dict | list | None,
    watt_fn: Callable[[float, float], float],
) -> go.Figure | None:
    actual_watts = extract_metric(performance_data, "output")
    if not actual_watts:
        return None

    seconds = list(range(len(actual_watts)))
    band = build_target_band(target_data, watt_fn)
    has_band = band is not None

    tick_every = 60
    tickvals = list(range(0, len(seconds), tick_every))
    ticktext = [f"{v // 60}:{v % 60:02d}" for v in tickvals]

    if has_band:
        fig = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            row_heights=[0.5, 0.2, 0.3],
            vertical_spacing=0.04,
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    # -- Target band (upper then lower with fill) --
    if has_band:
        band_s = band.index.tolist()
        floor_v = band["watt_floor"].tolist()
        ceil_v = band["watt_ceiling"].tolist()

        fig.add_trace(go.Scatter(
            x=band_s, y=ceil_v,
            mode="lines",
            line=dict(color="steelblue", width=1),
            showlegend=False,
            hoverinfo="skip",
        ), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=band_s, y=floor_v,
            mode="lines",
            fill="tonexty",
            fillcolor="rgba(70, 130, 180, 0.2)",
            line=dict(color="steelblue", width=1),
            name="Target band",
        ), row=1, col=1)

    # -- Actual watts --
    fig.add_trace(go.Scatter(
        x=seconds, y=actual_watts,
        mode="lines",
        line=dict(color="tomato", width=1.3),
        name="Actual output (W)",
    ), row=1, col=1)

    if has_band:
        # -- Band position % --
        positions: list[float | None] = []
        for sec, w in enumerate(actual_watts):
            if sec in band.index:
                fl = band.at[sec, "watt_floor"]
                ce = band.at[sec, "watt_ceiling"]
                span = ce - fl
                pct = ((w - fl) / span * 100) if span > 0 else 50.0
            else:
                pct = None
            positions.append(pct)

        fig.add_trace(go.Scatter(
            x=seconds, y=positions,
            mode="lines",
            line=dict(color="mediumpurple", width=1),
            name="Band position %",
        ), row=2, col=1)

        for y_val, color, dash in [(0, "steelblue", "dash"), (100, "steelblue", "dash"), (50, "gray", "dot")]:
            fig.add_hline(y=y_val, line_color=color, line_dash=dash, line_width=0.8, row=2, col=1)

        # -- Cumulative kJ --
        cum_actual = np.cumsum(actual_watts) / 1000
        band_reindexed = band.reindex(seconds, fill_value=0)
        cum_max = np.cumsum(band_reindexed["watt_ceiling"].values) / 1000
        cum_min = np.cumsum(band_reindexed["watt_floor"].values) / 1000

        fig.add_trace(go.Scatter(
            x=seconds, y=cum_max,
            mode="lines",
            line=dict(color="steelblue", width=1),
            showlegend=False,
            hoverinfo="skip",
        ), row=3, col=1)
        fig.add_trace(go.Scatter(
            x=seconds, y=list(cum_min),
            mode="lines",
            fill="tonexty",
            fillcolor="rgba(70, 130, 180, 0.2)",
            line=dict(color="steelblue", width=1),
            name="Target range (kJ)",
        ), row=3, col=1)
        fig.add_trace(go.Scatter(
            x=seconds, y=list(cum_actual),
            mode="lines",
            line=dict(color="tomato", width=1.3),
            name="Actual (kJ)",
        ), row=3, col=1)

    # -- Layout --
    title = meta.get("title", "Peloton Workout")
    instructor = meta.get("instructor_name", "")
    header = f"<b>{title}{'  ·  ' + instructor if instructor else ''}</b>"

    ts = meta.get("start_time", 0)
    date_str = datetime.fromtimestamp(ts).strftime("%b %d, %Y  %I:%M %p") if ts else ""
    kj = (meta.get("total_work") or 0) / 1000
    sub = f"{date_str}  ·  {kj:.0f} kJ total output" if kj else date_str
    if not has_band:
        sub += "  (no instructor target data available)"

    fig.update_layout(
        title=dict(text=f"{header}<br><sup>{sub}</sup>", x=0.5, xanchor="center"),
        height=780 if has_band else 420,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=110),
    )

    last_row = 3 if has_band else 1
    fig.update_xaxes(tickvals=tickvals, ticktext=ticktext, title_text="Time into ride",
                     row=last_row, col=1)
    fig.update_yaxes(title_text="Watts", row=1, col=1)
    if has_band:
        fig.update_yaxes(title_text="Band %", range=[-40, 140], row=2, col=1)
        fig.update_yaxes(title_text="Cumulative kJ", rangemode="tozero", row=3, col=1)

    return fig


# ---------------------------------------------------------------------------
# Data smoothing
# ---------------------------------------------------------------------------

def smooth_series(values: list[float], window: int) -> list[float]:
    """Centered rolling mean to reduce per-second noise in time-series data."""
    if window <= 1 or not values:
        return values
    return pd.Series(values).rolling(window, min_periods=1, center=True).mean().tolist()


# ---------------------------------------------------------------------------
# Shared x-axis tick helper
# ---------------------------------------------------------------------------

def _x_ticks(n: int) -> dict:
    vals = list(range(0, n, 60))
    return dict(
        tickvals=vals,
        ticktext=[f"{v // 60}:{v % 60:02d}" for v in vals],
        title_text="Time into ride",
    )


# ---------------------------------------------------------------------------
# Individual chart functions (used by the Streamlit app for reorderable panels)
# ---------------------------------------------------------------------------

def plot_watts_chart(
    meta: dict,
    seconds: list[int],
    actual_watts: list[float],
    band: pd.DataFrame | None,
    y_max: int = 0,
) -> go.Figure:
    fig = go.Figure()

    if band is not None:
        bs = band.index.tolist()
        fig.add_trace(go.Scatter(
            x=bs, y=band["watt_ceiling"].tolist(),
            mode="lines", line=dict(color="steelblue", width=1),
            showlegend=False, hoverinfo="skip",
        ))
        fig.add_trace(go.Scatter(
            x=bs, y=band["watt_floor"].tolist(),
            mode="lines", fill="tonexty",
            fillcolor="rgba(70, 130, 180, 0.2)",
            line=dict(color="steelblue", width=1),
            name="Target band",
        ))

    fig.add_trace(go.Scatter(
        x=seconds, y=actual_watts,
        mode="lines", line=dict(color="tomato", width=1.3),
        name="Actual output (W)",
    ))

    title = meta.get("title", "Peloton Workout")
    instructor = meta.get("instructor_name", "")
    header = f"<b>{title}{'  ·  ' + instructor if instructor else ''}</b>"
    ts = meta.get("start_time", 0)
    date_str = datetime.fromtimestamp(ts).strftime("%b %d, %Y  %I:%M %p") if ts else ""
    kj = (meta.get("total_work") or 0) / 1000
    sub = f"{date_str}  ·  {kj:.0f} kJ total output" if kj else date_str
    if band is None:
        sub += "  (no instructor target data available)"

    y_axis: dict = dict(title_text="Watts")
    if y_max > 0:
        y_axis["range"] = [0, y_max]

    fig.update_layout(
        title=dict(text=f"{header}<br><sup>{sub}</sup>", x=0.5, xanchor="center"),
        height=560, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=110),
        xaxis=_x_ticks(len(seconds)),
        yaxis=y_axis,
    )
    return fig


def plot_band_position_chart(
    seconds: list[int],
    actual_watts: list[float],
    band: pd.DataFrame,
) -> go.Figure:
    positions: list[float | None] = []
    for sec, w in enumerate(actual_watts):
        if sec in band.index:
            fl = band.at[sec, "watt_floor"]
            ce = band.at[sec, "watt_ceiling"]
            span = ce - fl
            positions.append(((w - fl) / span * 100) if span > 0 else 50.0)
        else:
            positions.append(None)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=seconds, y=positions,
        mode="lines", line=dict(color="mediumpurple", width=1),
        name="Total Output %",
    ))
    for y_val, color, dash in [(0, "steelblue", "dash"), (100, "steelblue", "dash"), (50, "gray", "dot")]:
        fig.add_hline(y=y_val, line_color=color, line_dash=dash, line_width=0.8)

    fig.update_layout(
        height=380, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=20),
        xaxis=_x_ticks(len(seconds)),
        yaxis=dict(title_text="Output %"),
    )
    return fig


def plot_cumulative_chart(
    seconds: list[int],
    actual_watts: list[float],
    band: pd.DataFrame,
) -> go.Figure:
    cum_actual = np.cumsum(actual_watts) / 1000
    band_re = band.reindex(seconds, fill_value=0)
    cum_max = np.cumsum(band_re["watt_ceiling"].values) / 1000
    cum_min = np.cumsum(band_re["watt_floor"].values) / 1000

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=seconds, y=list(cum_max),
        mode="lines", line=dict(color="steelblue", width=1),
        showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=seconds, y=list(cum_min),
        mode="lines", fill="tonexty",
        fillcolor="rgba(70, 130, 180, 0.2)",
        line=dict(color="steelblue", width=1),
        name="Target range (kJ)",
    ))
    fig.add_trace(go.Scatter(
        x=seconds, y=list(cum_actual),
        mode="lines", line=dict(color="tomato", width=1.3),
        name="Actual (kJ)",
    ))

    fig.update_layout(
        title=dict(text="Cumulative Output (kJ)", x=0.5, xanchor="center"),
        height=420, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=60),
        xaxis=_x_ticks(len(seconds)),
        yaxis=dict(title_text="kJ", rangemode="tozero"),
    )
    return fig
