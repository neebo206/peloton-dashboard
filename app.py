"""Peloton Dashboard — Streamlit web app."""

import contextlib
import io
import random
import time
from datetime import datetime

import requests
import streamlit as st

try:
    from streamlit_sortables import sort_items
    _HAS_SORTABLES = True
except ImportError:
    _HAS_SORTABLES = False

_INSTRUCTORS = [
    "Olivia", "Robin", "Alex", "Emma", "Cody",
    "Ally", "Christine", "Hannah", "Leanne",
    "Matt", "Jess", "Denis", "Camila", "Ben",
]

# Maps the first name we use internally to the exact full name in the Peloton API
_INSTRUCTOR_FULLNAMES = {
    "Olivia":    "Olivia Amato",
    "Robin":     "Robin Arzon",
    "Alex":      "Alex Toussaint",
    "Emma":      "Emma Lovewell",
    "Cody":      "Cody Rigsby",
    "Ally":      "Ally Love",
    "Christine": "Christine D'Ercole",
    "Hannah":    "Hannah Frankson",
    "Leanne":    "Leanne Hainsby-Alldis",
    "Matt":      "Matt Wilpers",
    "Jess":      "Jess King",
    "Denis":     "Denis Morton",
    "Camila":    "Camila Ramón",
    "Ben":       "Ben Alldis",
}

_INSTRUCTOR_AFFIRMATIONS = {
    "Olivia":    "Let's go! You showed up — that's everything.",
    "Robin":     "Sweat is your magic. You just proved it.",
    "Alex":      "That's what I'm talking about, family! Let's ride!",
    "Emma":      "Balance, strength, joy — you've got all three. Let's do this.",
    "Cody":      "Okurr! You're in, boo. Let's get it!",
    "Ally":      "You are loved, you are worthy, and you are HERE. Let's go!",
    "Christine": "I am, I can, I will, I do. You just did.",
    "Hannah":    "You've absolutely got this. Brilliant work getting here.",
    "Leanne":    "Gorgeous effort getting here. Now let's make it count.",
    "Matt":      "Metrics don't lie — and neither does showing up. Let's go.",
    "Jess":      "Welcome to the experience. It's going to be everything.",
    "Denis":     "Ride your own ride. It starts right now.",
    "Camila":    "¡Vamos! You're here and you're ready. Let's go!",
    "Ben":       "No excuses, no shortcuts — just work. Let's get after it.",
}


def _instructor_img_html(img_url: str, caption: str) -> str:
    """Centered instructor photo with a Peloton-gradient oval ring."""
    caption_html = (
        f'<p style="text-align:center;font-size:0.82rem;color:#888;margin-top:6px;font-style:italic">{caption}</p>'
        if caption else ""
    )
    return (
        '<div style="display:flex;flex-direction:column;align-items:center;margin:12px 0">'
        '<div style="background:linear-gradient(135deg,#e83c5a,#ff9a3c);'
        'border-radius:50%;padding:3px;width:158px;height:170px;">'
        '<div style="border-radius:50%;overflow:hidden;width:152px;height:164px;">'
        f'<img src="{img_url}" style="width:100%;height:100%;object-fit:cover;object-position:top center">'
        f'</div></div>{caption_html}</div>'
    )


@st.cache_data(ttl=86400)
def _instructor_images() -> dict[str, str]:
    """Fetch first-name → image_url from Peloton's public instructor endpoint."""
    try:
        r = requests.get(
            "https://api.onepeloton.com/api/instructor",
            params={"limit": 100},
            headers={"Peloton-Platform": "web"},
            timeout=5,
        )
        if r.status_code != 200:
            return {}
        return {
            item["name"]: item["image_url"]
            for item in r.json().get("data", [])
            if item.get("image_url")
        }
    except Exception:
        return {}

from chart import (
    build_target_band,
    extract_metric,
    plot_band_position_chart_altair as plot_band_position_chart,
    plot_cumulative_chart_altair as plot_cumulative_chart,
    plot_watts_chart_altair as plot_watts_chart,
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
                full_name = _INSTRUCTOR_FULLNAMES.get(instructor, instructor)
                img_url = _instructor_images().get(full_name, "")
                if img_url:
                    st.markdown(
                        _instructor_img_html(img_url, ""),
                        unsafe_allow_html=True,
                    )

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
                    except Exception as exc:
                        error_spot.error(f"Login failed: {exc}")

                if "peloton_token" in st.session_state:
                    affirmation = _INSTRUCTOR_AFFIRMATIONS.get(instructor, "You've got this!")
                    st.markdown(
                        '<p style="text-align:center;font-size:1.5rem;font-weight:700;color:#28a745">'
                        "✅ Alright — you're in!</p>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<p style="text-align:center;font-style:italic;font-size:1rem;color:#555">'
                        f'💪 &ldquo;{affirmation}&rdquo;</p>',
                        unsafe_allow_html=True,
                    )
                    time.sleep(2)
                    st.rerun()


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

# Suppress the opacity fade Streamlit applies to stale elements during fragment reruns
st.markdown(
    "<style>"
    "[data-stale]{opacity:1!important;transition:none!important;animation:none!important}"
    "[data-stale] *{opacity:1!important;transition:none!important;animation:none!important}"
    "</style>",
    unsafe_allow_html=True,
)


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
        st.caption("🔴 Live tracking enabled")
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
# Cached data fetchers
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

workout    = workouts[idx]
ride       = workout.get("ride") or {}
ride_id    = ride.get("id")
workout_id = workout["id"]
instructor = (ride.get("instructor") or {}).get("name", "")
ts         = workout.get("start_time", 0)
kj         = (workout.get("total_work") or 0) / 1000
duration   = ride.get("duration", 0)
has_real_ride = bool(ride_id and set(ride_id) != {"0"})

meta = {
    "title":           ride.get("title", "Peloton Workout"),
    "instructor_name": instructor,
    "duration":        duration,
    "start_time":      ts,
    "total_work":      workout.get("total_work", 0),
}


# ---------------------------------------------------------------------------
# Chart order — initialise and show sortable in sidebar
# ---------------------------------------------------------------------------

_ALL_CHARTS = ["cumulative", "watts", "band_position"]
_CHART_LABELS = {
    "cumulative":    "Cumulative Output (kJ)",
    "watts":         "Output (W)",
    "band_position": "Total Output Percentage",
}
_LABEL_TO_ID = {v: k for k, v in _CHART_LABELS.items()}

if has_real_ride:
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


# ---------------------------------------------------------------------------
# Fragment: fetch live data + render charts
# Reruns on its own timer in live mode — no full-page fade.
# ---------------------------------------------------------------------------

@st.fragment(run_every=refresh_interval if live_ride else None)
def _render_charts() -> None:
    if live_ride:
        st.toast("Refreshing...", icon="🔄")
        perf   = client.get_performance_graph(workout_id)
        target = client.get_target_metrics(ride_id) if has_real_ride else None
    else:
        perf   = get_performance(client.user_id, workout_id)
        target = get_target(client.user_id, ride_id) if has_real_ride else None

    actual_w = extract_metric(perf, "output")

    # Summary metrics — total output computed from fresh performance data
    live_kj = sum(actual_w) / 1000 if actual_w else kj
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Date", datetime.fromtimestamp(ts).strftime("%b %d, %Y  %I:%M %p") if ts else "—")
    c2.metric("Instructor", instructor or "—")
    c3.metric("Duration", f"{duration // 60} min" if duration else "—")
    c4.metric("Total output", f"{live_kj:.1f} kJ" if live_kj else "—")

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

    if not actual_w:
        st.error("No output data found for this workout.")
        return

    seconds    = list(range(len(actual_w)))
    smoothed_w = smooth_series(actual_w, smooth_n)
    band       = build_target_band(target, estimate_watts)
    has_band   = band is not None

    charts = list(st.session_state.get("chart_order", _ALL_CHARTS)) if has_band else ["watts"]

    _tooltip = (
        "Total output as a percentage of the maximum score defined by "
        "upper limits of the instructor's cadence and resistance."
    )

    for chart_id in charts:
        if chart_id == "watts":
            st.altair_chart(
                plot_watts_chart(meta, seconds, smoothed_w, band, y_max),
                use_container_width=True,
            )
        elif chart_id == "band_position" and band is not None:
            st.markdown(
                f'<p style="font-size:1rem;font-weight:600;cursor:help;margin-bottom:0" '
                f'title="{_tooltip}">Total Output Percentage &nbsp;&#x2139;&#xFE0F;</p>',
                unsafe_allow_html=True,
            )
            st.altair_chart(
                plot_band_position_chart(seconds, smoothed_w, band),
                use_container_width=True,
            )
        elif chart_id == "cumulative" and band is not None:
            st.altair_chart(
                plot_cumulative_chart(seconds, actual_w, band),
                use_container_width=True,
            )


_render_charts()
