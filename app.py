"""Peloton Dashboard — Streamlit web app."""

import contextlib
import io
import random
from datetime import datetime

import streamlit as st

_INSTRUCTORS = [
    "Olivia", "Robin", "Alex", "Emma", "Cody",
    "Ally", "Christine", "Hannah", "Kendall", "Leanne",
    "Matt", "Jess", "Denis", "Camila", "Ben",
]

from chart import extract_metric, plot_workout_plotly
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

        if submitted:
            if not email or not password:
                st.error("Enter your email/username and password.")
                return
            instructor = random.choice(_INSTRUCTORS)
            with st.spinner(f"Checking with {instructor} at Peloton to see if you're legit..."):
                try:
                    token = PelotonClient.get_token_via_playwright(email, password)
                    st.session_state.peloton_token = token
                    st.session_state.peloton_email = email
                    st.session_state.login_instructor = instructor
                    st.cache_data.clear()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Login failed: {exc}")


if "peloton_token" not in st.session_state:
    _show_login()
    st.stop()


# ---------------------------------------------------------------------------
# Authenticated — build client from session token
# ---------------------------------------------------------------------------

token = st.session_state.peloton_token

# Refresh token if it's near expiry (re-runs Playwright silently)
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
    st.header("Settings")
    limit = st.number_input("Workouts to load", min_value=5, max_value=100, value=20, step=5)
    if st.button("Refresh workout list", use_container_width=True):
        st.cache_data.clear()
        st.rerun()


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
# Chart
# ---------------------------------------------------------------------------

meta = {
    "title":           ride.get("title", "Peloton Workout"),
    "instructor_name": instructor,
    "duration":        duration,
    "start_time":      ts,
    "total_work":      workout.get("total_work", 0),
}

fig = plot_workout_plotly(meta, perf, target, estimate_watts)
if fig:
    st.plotly_chart(fig, use_container_width=True)
else:
    st.error("No output data found for this workout.")
