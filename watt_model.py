"""
Polynomial approximation of Peloton's internal cadence×resistance → watts table.

The model is fit from community-collected empirical data on magnetic flywheel
bikes. It will never be exact because Peloton's actual lookup table is
proprietary and differs slightly per bike.

Run calibrate() after fetching a completed workout to see how far off the
model is on your specific machine.
"""

import numpy as np


def estimate_watts(cadence: float, resistance: float) -> float:
    """
    Estimate power output (W) from cadence (RPM) and resistance (0–100 %).

    Returns 0.0 for zero/negative inputs; wattage grows roughly quadratically
    with resistance and linearly with cadence.
    """
    if resistance <= 0 or cadence <= 0:
        return 0.0

    c, r = float(cadence), float(resistance)

    watts = (
        -9.48
        + 0.123 * c
        + 0.452 * r
        + 0.011 * c * r
        + 0.00021 * c ** 2
        + 0.0412 * r ** 2
    )
    return max(0.0, round(watts, 1))


def calibrate(
    actual_cadence: list[float],
    actual_resistance: list[float],
    actual_watts: list[float],
) -> None:
    """
    Compare model estimates to measured watts from a completed workout.

    Prints mean error, MAE, and range so you can judge whether the model
    needs a manual bias correction for your bike.
    """
    pairs = [
        (c, r, w)
        for c, r, w in zip(actual_cadence, actual_resistance, actual_watts)
        if w and w > 0 and c and c > 0 and r and r > 0
    ]
    if not pairs:
        print("  Calibration: no valid data points (cadence/resistance/output all > 0).")
        return

    estimated = np.array([estimate_watts(c, r) for c, r, _ in pairs])
    actual = np.array([w for _, _, w in pairs])
    errors = estimated - actual

    print(
        f"  Watt model vs bike  —  "
        f"mean error {np.mean(errors):+.1f} W  |  "
        f"MAE {np.mean(np.abs(errors)):.1f} W  |  "
        f"range [{errors.min():+.0f}, {errors.max():+.0f}] W  "
        f"({len(pairs)} samples)"
    )
    if abs(np.mean(errors)) > 15:
        print(
            "  ⚠  Mean error > 15 W — the target band will be offset. "
            "The chart will still show relative position correctly."
        )
