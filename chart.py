"""Chart rendering for the Peloton post-workout dashboard."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Target band extraction
# ---------------------------------------------------------------------------

def _find_intervals(data: dict | list) -> list[dict] | None:
    """
    Walk common API response shapes to locate the segment/interval list.
    Returns None if nothing recognisable is found.
    """
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
    """
    Convert raw target_metrics API payload to a second-indexed DataFrame
    with columns: watt_floor, watt_ceiling, cadence_low, cadence_high,
    resistance_low, resistance_high.

    Returns None if the payload is missing or unrecognisable.
    """
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
            records.append(
                {
                    "second": sec,
                    "watt_floor": floor_w,
                    "watt_ceiling": ceiling_w,
                    "cadence_low": c_min,
                    "cadence_high": c_max,
                    "resistance_low": r_min,
                    "resistance_high": r_max,
                }
            )

    if not records:
        return None

    return pd.DataFrame(records).set_index("second")


# ---------------------------------------------------------------------------
# Performance graph helpers
# ---------------------------------------------------------------------------

def extract_metric(performance_data: dict, slug: str) -> list[float]:
    """Pull a named metric's value array from a performance_graph response."""
    for m in performance_data.get("metrics", []):
        if m.get("slug") == slug:
            return [v or 0.0 for v in m.get("values", [])]
    # Also check averages_and_summaries section
    for m in performance_data.get("averages", []):
        if m.get("slug") == slug:
            return [v or 0.0 for v in m.get("values", [])]
    return []


# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

def _mm_ss(x: float, _) -> str:
    x = max(0, int(x))
    return f"{x // 60}:{x % 60:02d}"


def plot_workout(
    meta: dict,
    performance_data: dict,
    target_data: dict | list | None,
    watt_fn: Callable[[float, float], float],
) -> None:
    """
    Render the output band chart and display it.

    meta keys expected:
        title, instructor_name, duration, start_time, total_work
    """
    title = meta.get("title", "Peloton Workout")
    instructor = meta.get("instructor_name", "")
    start_ts = meta.get("start_time", 0)
    total_kj = (meta.get("total_work") or 0) / 1000

    started = (
        datetime.fromtimestamp(start_ts).strftime("%b %d, %Y  %I:%M %p")
        if start_ts
        else ""
    )

    # -- Raw series --
    actual_watts = extract_metric(performance_data, "output")
    if not actual_watts:
        print("No output data found in performance graph — cannot render chart.")
        return

    seconds = list(range(len(actual_watts)))

    # -- Target band --
    band = build_target_band(target_data, watt_fn)
    has_band = band is not None

    # -- Layout --
    if has_band:
        fig, (ax_main, ax_pos, ax_cum) = plt.subplots(
            3, 1,
            figsize=(16, 12),
            gridspec_kw={"height_ratios": [3, 1, 2]},
            sharex=True,
        )
    else:
        fig, ax_main = plt.subplots(figsize=(16, 6))
        ax_pos = None
        ax_cum = None

    # -- Target band fill --
    if has_band:
        band_s = band.index.tolist()
        floor_v = band["watt_floor"].tolist()
        ceil_v = band["watt_ceiling"].tolist()

        ax_main.fill_between(
            band_s, floor_v, ceil_v,
            alpha=0.18, color="steelblue", label="Target band",
        )
        ax_main.step(band_s, floor_v, where="post", color="steelblue",
                     linewidth=1.0, alpha=0.55, label="_nolegend_")
        ax_main.step(band_s, ceil_v, where="post", color="steelblue",
                     linewidth=1.0, alpha=0.55, label="_nolegend_")

    # -- Actual output --
    ax_main.plot(
        seconds, actual_watts,
        color="tomato", linewidth=1.3, label="Actual output (W)", zorder=3,
    )
    ax_main.set_ylabel("Watts", fontsize=11)
    ax_main.set_ylim(bottom=0)
    ax_main.grid(True, alpha=0.25)
    ax_main.legend(loc="upper left", fontsize=9)

    # -- Band position subplot --
    if has_band and ax_pos is not None:
        positions: list[float] = []
        for sec, w in enumerate(actual_watts):
            if sec in band.index:
                fl = band.at[sec, "watt_floor"]
                ce = band.at[sec, "watt_ceiling"]
                span = ce - fl
                pct = ((w - fl) / span * 100) if span > 0 else 50.0
            else:
                pct = float("nan")
            positions.append(pct)

        ax_pos.plot(seconds, positions, color="mediumpurple", linewidth=1.0,
                    label="Band position %")
        ax_pos.axhline(0, color="steelblue", linewidth=0.8, linestyle="--", alpha=0.5)
        ax_pos.axhline(100, color="steelblue", linewidth=0.8, linestyle="--", alpha=0.5)
        ax_pos.axhline(50, color="gray", linewidth=0.6, linestyle=":", alpha=0.6)
        ax_pos.set_ylabel("Band %", fontsize=9)
        ax_pos.set_ylim(-40, 140)
        ax_pos.grid(True, alpha=0.2)
        ax_pos.legend(loc="upper left", fontsize=8)

    # -- Cumulative output subplot --
    if has_band and ax_cum is not None:
        cum_actual = np.cumsum(actual_watts) / 1000  # watts·s → kJ

        # Reindex band to every second of the ride, fill gaps with 0
        band_reindexed = band.reindex(seconds, fill_value=0)
        cum_max = np.cumsum(band_reindexed["watt_ceiling"].values) / 1000
        cum_min = np.cumsum(band_reindexed["watt_floor"].values) / 1000

        ax_cum.fill_between(seconds, cum_min, cum_max,
                            alpha=0.18, color="steelblue", label="Target range (kJ)")
        ax_cum.step(seconds, cum_max, where="post", color="steelblue",
                    linewidth=0.8, alpha=0.55, label="_nolegend_")
        ax_cum.step(seconds, cum_min, where="post", color="steelblue",
                    linewidth=0.8, alpha=0.55, label="_nolegend_")
        ax_cum.plot(seconds, cum_actual, color="tomato", linewidth=1.3,
                    label="Actual (kJ)", zorder=3)
        ax_cum.set_ylabel("Cumulative kJ", fontsize=9)
        ax_cum.set_ylim(bottom=0)
        ax_cum.grid(True, alpha=0.2)
        ax_cum.legend(loc="upper left", fontsize=8)
        ax_cum.xaxis.set_major_formatter(mticker.FuncFormatter(_mm_ss))
        ax_cum.set_xlabel("Time into ride", fontsize=10)
    else:
        ax_main.xaxis.set_major_formatter(mticker.FuncFormatter(_mm_ss))
        ax_main.set_xlabel("Time into ride", fontsize=10)

    # -- Titles --
    header = title + (f"  ·  {instructor}" if instructor else "")
    sub = started + (f"  ·  {total_kj:.0f} kJ total output" if total_kj else "")
    if not has_band:
        sub += "  (no instructor target data available)"

    fig.suptitle(header, fontsize=13, fontweight="bold", y=0.98)
    ax_main.set_title(sub, fontsize=9, pad=6)

    plt.tight_layout()
    plt.show()
