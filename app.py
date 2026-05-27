"""Peloton Dashboard — Streamlit web app."""

import contextlib
import io
from datetime import datetime
from pathlib import Path

import streamlit as st

from chart import extract_metric, plot_workout_plotly
from client import PelotonClient
from watt_model import calibrate, estimate_watts

CONFIG_PATH = Path(__file__).parent / "config.json"

st.set_page_config(page_title="Peloton Dashboard", layout="wide")
st.title("Peloton Dashboard")


# ---------------------------------------------------------------------------
# Cached resources
# ---------------------------------------------------------------------------

@st.cache_resource
def get_client() -> PelotonClient:
    client = PelotonClient(CONFIG_PATH)
    client.login()
    return client


@st.cache_data
def get_workouts(_client: PelotonClient, limit: int) -> list[dict]:
    return _client.get_workouts(limit=limit)


@st.cache_data
def get_performance(_client: PelotonClient, workout_id: str) -> dict:
    return _client.get_performance_graph(workout_id)


@st.cache_data
def get_target(_client: PelotonClient, ride_id: str) -> dict | None:
    return _client.get_target_metrics(ride_id)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

try:
    client = get_client()
except FileNotFoundError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(f"Login failed: {e}")
    st.stop()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Settings")
    limit = st.number_input("Workouts to load", min_value=5, max_value=100, value=20, step=5)
    if st.button("Refresh workout list"):
        st.cache_data.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Ride selector
# ---------------------------------------------------------------------------

with st.spinner("Loading workouts..."):
    workouts = get_workouts(client, int(limit))

if not workouts:
    st.warning("No cycling workouts found for this account.")
    st.stop()


def _label(w: dict) -> str:
    ride = w.get("ride") or {}
    title = ride.get("title", "Unknown")
    instructor = (ride.get("instructor") or {}).get("name", "")
    ts = w.get("start_time", 0)
    date = datetime.fromtimestamp(ts).strftime("%b %d, %Y") if ts else "?"
    kj = (w.get("total_work") or 0) / 1000
    parts = [date, title]
    if instructor:
        parts.append(instructor)
    if kj:
        parts.append(f"{kj:.0f} kJ")
    return "  ·  ".join(parts)


labels = [_label(w) for w in workouts]
idx = st.selectbox("Select a ride", range(len(labels)), format_func=lambda i: labels[i])

workout = workouts[idx]
ride = workout.get("ride") or {}
ride_id = ride.get("id")
workout_id = workout["id"]
instructor = (ride.get("instructor") or {}).get("name", "")
ts = workout.get("start_time", 0)
kj = (workout.get("total_work") or 0) / 1000
duration = ride.get("duration", 0)

# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

c1, c2, c3, c4 = st.columns(4)
c1.metric("Date", datetime.fromtimestamp(ts).strftime("%b %d, %Y  %I:%M %p") if ts else "—")
c2.metric("Instructor", instructor or "—")
c3.metric("Duration", f"{duration // 60} min" if duration else "—")
c4.metric("Total output", f"{kj:.0f} kJ" if kj else "—")

# ---------------------------------------------------------------------------
# Fetch ride data
# ---------------------------------------------------------------------------

with st.spinner("Fetching ride data..."):
    perf = get_performance(client, workout_id)
    has_real_ride = ride_id and set(ride_id) != {"0"}
    target = get_target(client, ride_id) if has_real_ride else None

# ---------------------------------------------------------------------------
# Watt model accuracy
# ---------------------------------------------------------------------------

actual_w = extract_metric(perf, "output")
cad = extract_metric(perf, "cadence")
res = extract_metric(perf, "resistance")

if actual_w and cad and res:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        calibrate(cad, res, actual_w)
    cal_text = buf.getvalue().strip()
    if cal_text:
        with st.expander("Watt model accuracy"):
            st.code(cal_text)

# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------

meta = {
    "title": ride.get("title", "Peloton Workout"),
    "instructor_name": instructor,
    "duration": duration,
    "start_time": ts,
    "total_work": workout.get("total_work", 0),
}

fig = plot_workout_plotly(meta, perf, target, estimate_watts)
if fig:
    st.plotly_chart(fig, use_container_width=True)
else:
    st.error("No output data found for this workout.")
