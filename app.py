"""Peloton Dashboard — Streamlit web app."""

import contextlib
import io
import random
from datetime import datetime

import streamlit as st

try:
    from streamlit_autorefresh import st_autorefresh
    _HAS_AUTOREFRESH = True
except ImportError:
    _HAS_AUTOREFRESH = False

try:
    from streamlit_sortables import sort_items
    _HAS_SORTABLES = True
except ImportError:
    _HAS_SORTABLES = False

_INSTRUCTORS = [
    "Olivia", "Robin", "Alex", "Emma", "Cody",
    "Ally", "Christine", "Hannah", "Kendall", "Leanne",
    "Matt", "Jess", "Denis", "Camila", "Ben",
]

from chart import (
    build_target_band,
    extract_metric,
    plot_band_position_chart,
    plot_cumulative_chart,
    plot_watts_chart,
    smooth_series,
)
from client import PelotonClient
from watt_model import calibrate, estimate_watts

st.set_page_config(page_title="Peloton Dashboard", layout="wide")


# ---------------------------------------------------------------------------
# Login screen
# ---------------------------------------------------------------------------

def _show_login() -> None:
    st.title("Peloton Dashboard")
    _, col, _ = st.columns([1, 1, 1])
    with col:
        st.subheader("Sign in")
        with st.form("login_form"):
            email    = st.text_input("Email or username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)

        error_spot = st.empty()

        if submitted:
            if not email or not password:
                error_spot.error("Enter your email/username and password.")
            else:
                instructor = random.choice(_INSTRUCTORS)
                with st.spinner(f"Checking with {instructor} at Peloton to see if you're legit..."):
                    try:
                        token = PelotonClient.get_token_via_playwright(email, password)
                        if not PelotonClient.token_valid(token):
                            error_spot.error("Login returned an invalid token. Please try again.")
                        else:
                            st.session_state.peloton_token = token
                            st.session_state.peloton_email = email
                            st.session_state.login_instructor = instructor
                            st.cache_data.clear()
                            st.rerun()
                    except Exception as exc:
                        error_spot.error(f"Login failed: {exc}")


if "peloton_token" not in st.session_state:
    _show_login()
    st.stop()


# ---------------------------------------------------------------------------
# Authenticated — build client from session token
# ---------------------------------------------------------------------------

token = st.session_state.peloton_token

if not PelotonClient.token_valid(token):
    with st.spinner("Session expired — logging in again..."):
        try:
            email    = st.session_state.get("peloton_email", "")
            password = st.session_state.get("peloton_password", "")
            if email and password:
                token = PelotonClient.get_token_via_playwright(email, password)
                st.session_state.peloton_token = token
                st.cache_data.clear()
            else:
                del st.session_state.peloton_token
                st.rerun()
        except Exception:
            del st.session_state.peloton_token
            st.rerun()

client = PelotonClient.from_token(token)
st.title("Peloton Dashboard")

if "login_instructor" in st.session_state:
    instructor = st.session_state.pop("login_instructor")
    st.toast(f"{instructor} likes you! You're in.", icon="🚴")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.caption(f"Signed in as **{st.session_state.get('peloton_email', client.user_id)}**")
    if st.button("Logout", use_container_width=True):
        for key in ("peloton_token", "peloton_email", "peloton_password"):
            st.session_state.pop(key, None)
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.header("Live Ride")
    live_ride = st.toggle("Enable live tracking")
    if live_ride:
        refresh_interval = int(st.number_input(
            "Refresh interval (seconds)", min_value=5, max_value=60, value=5, step=5,
        ))
    else:
        refresh_interval = 5

    st.divider()
    st.header("Settings")
    limit = st.number_input("Workouts to load", min_value=5, max_value=100, value=20, step=5)
    smooth_n = int(st.number_input(
        "Smoothing window (seconds)", min_value=1, max_value=30, value=10, step=1,
        help="Rolling average over this many seconds. 1 = no smoothing.",
    ))
    y_max = int(st.number_input(
        "Y-axis max watts (0 = auto)", min_value=0, max_value=1000, value=0, step=50,
    ))
    if st.button("Refresh workout list", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


# ---------------------------------------------------------------------------
# Live ride — auto-refresh and cache invalidation
# ---------------------------------------------------------------------------

if live_ride:
    if _HAS_AUTOREFRESH:
        st_autorefresh(interval=refresh_interval * 1000, key="live_ride_refresh")
    st.cache_data.clear()
    st.info(f"Live tracking active — refreshing every {refresh_interval}s", icon="🔴")


# ---------------------------------------------------------------------------
# Cached data fetchers (keyed on user_id so switching accounts works cleanly)
# ---------------------------------------------------------------------------

@st.cache_data
def get_workouts(user_id: str, limit: int) -> list[dict]:
    return client.get_workouts(limit=limit)


@st.cache_data
def get_performance(user_id: str, workout_id: str) -> dict:
    return client.get_performance_graph(workout_id)


@st.cache_data
def get_target(user_id: str, ride_id: str) -> dict | None:
    return client.get_target_metrics(ride_id)


# ---------------------------------------------------------------------------
# Ride selector
# ---------------------------------------------------------------------------

with st.spinner("Loading workouts..."):
    workouts = get_workouts(client.user_id, int(limit))

if not workouts:
    st.warning("No cycling workouts found for this account.")
    st.stop()


def _label(w: dict) -> str:
    ride = w.get("ride") or {}
    title = ride.get("title", "Unknown")
    instructor_name = (ride.get("instructor") or {}).get("name", "")
    ts = w.get("start_time", 0)
    date = datetime.fromtimestamp(ts).strftime("%b %d, %Y") if ts else "?"
    kj = (w.get("total_work") or 0) / 1000
    parts = [date, title]
    if instructor_name:
        parts.append(instructor_name)
    if kj:
        parts.append(f"{kj:.0f} kJ")
    return "  ·  ".join(parts)


labels = [_label(w) for w in workouts]
idx = st.selectbox("Select a ride", range(len(labels)), format_func=lambda i: labels[i])

workout = workouts[idx]
ride    = workout.get("ride") or {}
ride_id    = ride.get("id")
workout_id = workout["id"]
instructor = (ride.get("instructor") or {}).get("name", "")
ts       = workout.get("start_time", 0)
kj       = (workout.get("total_work") or 0) / 1000
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
    perf   = get_performance(client.user_id, workout_id)
    has_real_ride = ride_id and set(ride_id) != {"0"}
    target = get_target(client.user_id, ride_id) if has_real_ride else None


# ---------------------------------------------------------------------------
# Watt model accuracy
# ---------------------------------------------------------------------------

actual_w = extract_metric(perf, "output")
cad      = extract_metric(perf, "cadence")
res      = extract_metric(perf, "resistance")

if actual_w and cad and res:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        calibrate(cad, res, actual_w)
    cal_text = buf.getvalue().strip()
    if cal_text:
        with st.expander("Watt model accuracy"):
            st.code(cal_text)


# ---------------------------------------------------------------------------
# Chart data prep
# ---------------------------------------------------------------------------

if not actual_w:
    st.error("No output data found for this workout.")
    st.stop()

meta = {
    "title":           ride.get("title", "Peloton Workout"),
    "instructor_name": instructor,
    "duration":        duration,
    "start_time":      ts,
    "total_work":      workout.get("total_work", 0),
}

seconds    = list(range(len(actual_w)))
smoothed_w = smooth_series(actual_w, smooth_n)
band       = build_target_band(target, estimate_watts)
has_band   = band is not None


# ---------------------------------------------------------------------------
# Chart order (drag-to-reorder)
# ---------------------------------------------------------------------------

_ALL_CHARTS = ["cumulative", "watts", "band_position"]
_CHART_LABELS = {
    "cumulative":    "Cumulative Output (kJ)",
    "watts":         "Output (W)",
    "band_position": "Band Position %",
}
_LABEL_TO_ID = {v: k for k, v in _CHART_LABELS.items()}

if has_band:
    if "chart_order" not in st.session_state:
        st.session_state.chart_order = list(_ALL_CHARTS)
    order = [c for c in st.session_state.chart_order if c in _ALL_CHARTS]
    for c in _ALL_CHARTS:
        if c not in order:
            order.append(c)
    st.session_state.chart_order = order

    if _HAS_SORTABLES:
        with st.sidebar:
            st.divider()
            st.header("Chart Order")
            st.caption("Drag to reorder")
            sorted_labels = sort_items([_CHART_LABELS[c] for c in st.session_state.chart_order])
            new_order = [_LABEL_TO_ID[l] for l in sorted_labels if l in _LABEL_TO_ID]
            if new_order != list(st.session_state.chart_order):
                st.session_state.chart_order = new_order

    charts = list(st.session_state.chart_order)
else:
    charts = ["watts"]


# ---------------------------------------------------------------------------
# Render charts
# ---------------------------------------------------------------------------

for chart_id in charts:
    if chart_id == "watts":
        st.plotly_chart(
            plot_watts_chart(meta, seconds, smoothed_w, band, y_max),
            use_container_width=True,
        )
    elif chart_id == "band_position" and band is not None:
        st.plotly_chart(
            plot_band_position_chart(seconds, smoothed_w, band),
            use_container_width=True,
        )
    elif chart_id == "cumulative" and band is not None:
        st.plotly_chart(
            plot_cumulative_chart(seconds, actual_w, band),
            use_container_width=True,
        )
