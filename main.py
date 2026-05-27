#!/usr/bin/env python3
"""
Peloton Post-Workout Dashboard — Phase 1
-----------------------------------------
Shows your actual output vs the instructor's cadence/resistance target band
for any completed cycling workout.

Usage
-----
  python main.py                   # uses active_profile in config.json
  python main.py --profile neil    # override which profile to use

Setup
-----
  1. Copy config.example.json → config.json
  2. Fill in your credentials
  3. pip install -r requirements.txt
  4. python main.py
"""

import argparse
import http.client
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from client import PelotonClient
from watt_model import estimate_watts, calibrate
from chart import plot_workout, extract_metric


CONFIG_PATH = Path(__file__).parent / "config.json"


# ---------------------------------------------------------------------------
# Profile switching
# ---------------------------------------------------------------------------

def switch_profile(profile_name: str) -> None:
    """Rewrite active_profile in config.json without touching other keys."""
    if not CONFIG_PATH.exists():
        print(f"config.json not found — copy config.example.json and fill it in.")
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    if profile_name not in config["profiles"]:
        available = list(config["profiles"].keys())
        print(f"Profile '{profile_name}' not found. Available: {available}")
        sys.exit(1)

    config["active_profile"] = profile_name
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Active profile switched to '{profile_name}'.")


# ---------------------------------------------------------------------------
# Workout selector
# ---------------------------------------------------------------------------

def _fmt_duration(seconds: int) -> str:
    return f"{seconds // 60} min"


def _fmt_ts(ts: int | float) -> str:
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts).strftime("%b %d  %I:%M %p")


def select_workout(workouts: list[dict]) -> dict:
    print(f"\n{'#':>3}  {'Date':18}  {'Dur':6}  {'Output':>8}  Class")
    print("─" * 80)
    for i, w in enumerate(workouts, 1):
        ride = w.get("ride") or {}
        title = ride.get("title", "Unknown")
        instructor = (ride.get("instructor") or {}).get("name", "")
        dur = _fmt_duration(ride.get("duration") or 0)
        date = _fmt_ts(w.get("start_time"))
        kj = (w.get("total_work") or 0) / 1000
        kj_str = f"{kj:.0f} kJ" if kj else "—"

        # Trim long titles so the row fits
        display = f"{title[:42]}"
        if instructor:
            display += f"  [{instructor}]"

        print(f"{i:>3}  {date:18}  {dur:6}  {kj_str:>8}  {display}")

    print("─" * 80)

    while True:
        try:
            raw = input(f"Select workout (1–{len(workouts)}), or q to quit: ").strip()
            if raw.lower() == "q":
                sys.exit(0)
            idx = int(raw) - 1
            if 0 <= idx < len(workouts):
                return workouts[idx]
        except (ValueError, EOFError):
            pass
        print(f"  Enter a number between 1 and {len(workouts)}.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Peloton post-workout dashboard")
    parser.add_argument(
        "--profile", "-p",
        help="Switch to a named profile in config.json before running",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=20,
        help="How many recent workouts to list (default 20)",
    )
    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Print raw HTTP request/response headers",
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
        http.client.HTTPConnection.debuglevel = 1

    if args.profile:
        switch_profile(args.profile)

    client = PelotonClient(CONFIG_PATH)
    client.login()

    print(f"\nFetching last {args.limit} cycling workouts...")
    workouts = client.get_workouts(limit=args.limit)

    if not workouts:
        print("No cycling workouts found for this account.")
        return

    workout = select_workout(workouts)
    workout_id = workout["id"]
    ride = workout.get("ride") or {}
    ride_id = ride.get("id")

    print(f"\nFetching performance data...")
    perf = client.get_performance_graph(workout_id)

    target = None
    if ride_id:
        print("Fetching instructor target metrics...")
        target = client.get_target_metrics(ride_id)
        if not target:
            print("  No target metrics found for this ride — output chart only.")

    # Calibrate watt model so the user knows how well it matches their bike
    actual_w = extract_metric(perf, "output")
    cad = extract_metric(perf, "cadence")
    res = extract_metric(perf, "resistance")
    if actual_w and cad and res:
        print("\nWatt model accuracy check:")
        calibrate(cad, res, actual_w)

    instructor_name = (ride.get("instructor") or {}).get("name", "")
    meta = {
        "title": ride.get("title", "Peloton Workout"),
        "instructor_name": instructor_name,
        "duration": ride.get("duration", 0),
        "start_time": workout.get("start_time", 0),
        "total_work": workout.get("total_work", 0),
    }

    print("\nRendering chart...")
    plot_workout(meta, perf, target, estimate_watts)


if __name__ == "__main__":
    main()
