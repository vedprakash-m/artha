#!/usr/bin/env python3
"""
parse_apple_health.py — Artha Apple Health Export Parser
=========================================================
Streams the Apple Health export.xml (can be 1-3 GB) and extracts
key biometric metrics without loading the full file into memory.

Usage:
  python scripts/parse_apple_health.py --zip /path/to/export.zip
  python scripts/parse_apple_health.py --xml /path/to/export.xml

Output: JSON summary to stdout with recent metrics, trends, and workouts.
"""

from __future__ import annotations
import argparse
import io
import json
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------------------
# Metrics to extract — Apple HK type → friendly name
# ---------------------------------------------------------------------------
METRICS = {
    "HKQuantityTypeIdentifierBodyMass":             "weight_kg",
    "HKQuantityTypeIdentifierBodyFatPercentage":    "body_fat_pct",
    "HKQuantityTypeIdentifierLeanBodyMass":         "lean_mass_kg",
    "HKQuantityTypeIdentifierHeight":               "height_m",
    "HKQuantityTypeIdentifierVO2Max":               "vo2max",
    "HKQuantityTypeIdentifierRestingHeartRate":     "resting_hr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv_sdnn",
    "HKQuantityTypeIdentifierHeartRate":            "heart_rate",
    "HKQuantityTypeIdentifierBloodPressureSystolic":  "bp_systolic",
    "HKQuantityTypeIdentifierBloodPressureDiastolic": "bp_diastolic",
    "HKQuantityTypeIdentifierStepCount":            "steps",
    "HKQuantityTypeIdentifierDistanceWalkingRunning": "distance_walk_run_m",
    "HKQuantityTypeIdentifierActiveEnergyBurned":   "active_calories",
    "HKQuantityTypeIdentifierBasalEnergyBurned":    "basal_calories",
    "HKQuantityTypeIdentifierFlightsClimbed":       "flights_climbed",
    "HKQuantityTypeIdentifierSleepDurationGoal":    "sleep_goal_h",
    "HKDataTypeSleepDurationGoal":                  "sleep_goal_h",
    "HKQuantityTypeIdentifierAppleExerciseTime":    "exercise_min",
    "HKQuantityTypeIdentifierAppleStandTime":       "stand_min",
    "HKCategoryTypeIdentifierSleepAnalysis":        "sleep_analysis",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_hr_avg",
    "HKQuantityTypeIdentifierWalkingSpeed":         "walking_speed",
    "HKQuantityTypeIdentifierWalkingAsymmetryPercentage": "walking_asymmetry_pct",
    "HKQuantityTypeIdentifierWalkingDoubleSupportPercentage": "walking_double_support_pct",
    "HKQuantityTypeIdentifierStairAscentSpeed":     "stair_ascent_speed",
    "HKQuantityTypeIdentifierStairDescentSpeed":    "stair_descent_speed",
}

WANT_TYPES = set(METRICS.keys())

# Workout type map (partial)
WORKOUT_TYPES = {
    "HKWorkoutActivityTypeHiking": "Hiking",
    "HKWorkoutActivityTypeRunning": "Running",
    "HKWorkoutActivityTypeWalking": "Walking",
    "HKWorkoutActivityTypeCycling": "Cycling",
    "HKWorkoutActivityTypeTraditionalStrengthTraining": "Strength",
    "HKWorkoutActivityTypeFunctionalStrengthTraining": "Strength",
    "HKWorkoutActivityTypeElliptical": "Elliptical",
    "HKWorkoutActivityTypeStairClimbing": "StairClimbing",
    "HKWorkoutActivityTypeMixedCardio": "MixedCardio",
    "HKWorkoutActivityTypeOther": "Other",
    "HKWorkoutActivityTypeCoreTraining": "Core",
    "HKWorkoutActivityTypeYoga": "Yoga",
    "HKWorkoutActivityTypeBadminton": "Badminton",
    "HKWorkoutActivityTypeSoccer": "Soccer",
}

TODAY = date.today()
WINDOW_RECENT   = TODAY - timedelta(days=90)   # 90-day window for latest/avg
WINDOW_TREND    = TODAY - timedelta(days=180)  # 180-day window for trend


def parse_date(s: str) -> date | None:
    """Parse Apple Health date strings like '2026-03-08 07:15:00 -0800'."""
    try:
        return datetime.fromisoformat(s.replace(" ", "T", 1)).date()
    except Exception:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def stream_parse(xml_stream: io.IOBase) -> dict:
    """Stream-parse the Apple Health XML and return aggregated metrics."""
    records: dict[str, list[tuple[date, float]]] = defaultdict(list)
    workouts: list[dict] = []
    me_info: dict = {}

    context = ET.iterparse(xml_stream, events=("start", "end"))
    _, root = next(context)   # grab root element

    for event, elem in context:
        if event != "end":
            continue

        tag = elem.tag

        # --- Me (personal info) ---
        if tag == "Me":
            me_info = dict(elem.attrib)
            elem.clear()
            continue

        # --- Quantity / Category records ---
        if tag == "Record":
            rtype = elem.get("type", "")
            if rtype not in WANT_TYPES:
                elem.clear()
                continue

            d_str = elem.get("endDate") or elem.get("startDate", "")
            d = parse_date(d_str)
            if d is None or d < WINDOW_TREND:
                elem.clear()
                continue

            value_str = elem.get("value", "")
            if rtype == "HKCategoryTypeIdentifierSleepAnalysis":
                # value = 0 (in bed) | 1 (asleep) | 2 (awake) | 3 (core) | 4 (deep) | 5 (rem)
                try:
                    val = float(value_str)
                except Exception:
                    elem.clear()
                    continue
                # Compute duration in hours from startDate → endDate
                start = parse_date(elem.get("startDate", ""))
                end   = parse_date(elem.get("endDate", ""))
                if start and end and start == end:
                    # same-day snippet; use seconds from full string parse
                    try:
                        s_dt = datetime.fromisoformat(elem.get("startDate", "").replace(" ", "T", 1))
                        e_dt = datetime.fromisoformat(elem.get("endDate", "").replace(" ", "T", 1))
                        duration_h = (e_dt - s_dt).total_seconds() / 3600
                        records["sleep_asleep_h"].append((d, duration_h)) if val in (1, 3, 4, 5) else None
                        records["sleep_inbed_h"].append((d, duration_h)) if val == 0 else None
                    except Exception:
                        pass
                elem.clear()
                continue

            try:
                val = float(value_str)
            except Exception:
                elem.clear()
                continue

            friendly = METRICS[rtype]
            records[friendly].append((d, val))
            elem.clear()
            continue

        # --- Workouts ---
        if tag == "Workout":
            d_str = elem.get("startDate", "")
            d = parse_date(d_str)
            if d is None or d < WINDOW_RECENT:
                elem.clear()
                continue

            wtype = WORKOUT_TYPES.get(elem.get("workoutActivityType", ""), elem.get("workoutActivityType", "Unknown"))
            duration_min = round(float(elem.get("duration", 0)), 1)
            distance_m   = None
            calories     = None
            for stat in elem.iter("WorkoutStatistics"):
                stype = stat.get("type", "")
                if "Distance" in stype:
                    try:
                        distance_m = float(stat.get("sum", 0))
                    except Exception:
                        pass
                if "ActiveEnergy" in stype or "EnergyBurned" in stype:
                    try:
                        calories = round(float(stat.get("sum", 0)), 0)
                    except Exception:
                        pass

            workouts.append({
                "date": d.isoformat(),
                "type": wtype,
                "duration_min": duration_min,
                "distance_km": round(distance_m / 1000, 2) if distance_m else None,
                "calories": calories,
            })
            elem.clear()
            continue

        # Free memory aggressively
        root.clear()

    return {
        "me": me_info,
        "records": {k: v for k, v in records.items()},
        "workouts": sorted(workouts, key=lambda x: x["date"], reverse=True),
    }


def summarize(data: dict) -> dict:
    """Compute summary stats from raw records."""
    records = data["records"]
    summary = {}

    def latest(key: str) -> tuple[str, float] | None:
        vals = [(d, v) for d, v in records.get(key, []) if d >= WINDOW_RECENT]
        if not vals:
            return None
        d, v = max(vals, key=lambda x: x[0])
        return d.isoformat(), round(v, 2)

    def avg_last_n(key: str, n_days: int = 30) -> float | None:
        cutoff = TODAY - timedelta(days=n_days)
        vals = [v for d, v in records.get(key, []) if d >= cutoff]
        return round(sum(vals) / len(vals), 2) if vals else None

    def trend_slope(key: str) -> str:
        """Simple trend: compare first-30d avg to last-30d avg over 90d window."""
        old_cutoff = WINDOW_RECENT
        mid_cutoff = TODAY - timedelta(days=60)
        recent_cutoff = TODAY - timedelta(days=30)
        old_vals = [v for d, v in records.get(key, []) if old_cutoff <= d < mid_cutoff]
        new_vals = [v for d, v in records.get(key, []) if d >= recent_cutoff]
        if not old_vals or not new_vals:
            return "→ insufficient data"
        old_avg = sum(old_vals) / len(old_vals)
        new_avg = sum(new_vals) / len(new_vals)
        delta = new_avg - old_avg
        pct = abs(delta) / old_avg * 100 if old_avg else 0
        if abs(delta) < 0.5 or pct < 1:
            return "→ stable"
        return f"↑ +{delta:.1f} ({pct:.0f}%)" if delta > 0 else f"↓ {delta:.1f} ({pct:.0f}%)"

    def daily_totals_avg(key: str, n_days: int = 30) -> float | None:
        """Sum per-day, then average daily total (for steps, calories, etc.)."""
        cutoff = TODAY - timedelta(days=n_days)
        day_sums: dict[date, float] = defaultdict(float)
        for d, v in records.get(key, []):
            if d >= cutoff:
                day_sums[d] += v
        if not day_sums:
            return None
        return round(sum(day_sums.values()) / len(day_sums), 0)

    # --- Weight ---
    w = latest("weight_kg")
    if w:
        d, v = w
        lbs = round(v * 2.20462, 1)
        summary["weight"] = {"date": d, "kg": v, "lbs": lbs, "trend_30d": trend_slope("weight_kg")}

    # --- VO2 Max ---
    v = latest("vo2max")
    if v:
        summary["vo2max"] = {"date": v[0], "value": v[1], "trend_30d": trend_slope("vo2max")}

    # --- Resting HR ---
    r = latest("resting_hr")
    if r:
        summary["resting_hr"] = {
            "date": r[0], "bpm": r[1],
            "avg_30d": avg_last_n("resting_hr", 30),
            "trend_30d": trend_slope("resting_hr"),
        }

    # --- HRV ---
    h = latest("hrv_sdnn")
    if h:
        summary["hrv_sdnn"] = {
            "date": h[0], "ms": h[1],
            "avg_30d": avg_last_n("hrv_sdnn", 30),
            "trend_30d": trend_slope("hrv_sdnn"),
        }

    # --- Blood Pressure ---
    bp_s = latest("bp_systolic")
    bp_d = latest("bp_diastolic")
    if bp_s or bp_d:
        summary["blood_pressure"] = {
            "systolic": {"date": bp_s[0], "mmhg": bp_s[1]} if bp_s else None,
            "diastolic": {"date": bp_d[0], "mmhg": bp_d[1]} if bp_d else None,
        }

    # --- Steps ---
    steps_avg = daily_totals_avg("steps", 30)
    if steps_avg:
        summary["steps_daily_avg_30d"] = int(steps_avg)

    # --- Active calories ---
    cal_avg = daily_totals_avg("active_calories", 30)
    if cal_avg:
        summary["active_calories_daily_avg_30d"] = int(cal_avg)

    # --- Exercise minutes ---
    ex_avg = daily_totals_avg("exercise_min", 30)
    if ex_avg:
        summary["exercise_min_daily_avg_30d"] = int(ex_avg)

    # --- Sleep ---
    sleep_avg = avg_last_n("sleep_asleep_h", 30)
    if sleep_avg:
        summary["sleep_avg_h_30d"] = sleep_avg

    # --- Body fat ---
    bf = latest("body_fat_pct")
    if bf:
        summary["body_fat_pct"] = {"date": bf[0], "pct": round(bf[1] * 100, 1) if bf[1] < 1 else bf[1]}

    # --- Walking speed / gait ---
    ws = avg_last_n("walking_speed", 30)
    if ws:
        summary["walking_speed_avg_30d"] = ws

    # --- Recent workouts (last 20) ---
    summary["recent_workouts"] = data["workouts"][:20]

    # --- Workout type frequency (90d) ---
    freq: dict[str, int] = defaultdict(int)
    for w in data["workouts"]:
        freq[w["type"]] += 1
    summary["workout_frequency_90d"] = dict(sorted(freq.items(), key=lambda x: -x[1]))

    return summary


def main():
    ap = argparse.ArgumentParser(description="Parse Apple Health export.zip/xml")
    ap.add_argument("--zip", help="Path to export.zip")
    ap.add_argument("--xml", help="Path to extracted export.xml")
    args = ap.parse_args()

    print("[apple_health] Parsing export... (large file — may take 1-2 min)", file=sys.stderr)

    if args.zip:
        with zipfile.ZipFile(args.zip, "r") as z:
            # Find the XML inside the zip
            xml_names = [n for n in z.namelist() if n.endswith("export.xml")]
            if not xml_names:
                print("ERROR: export.xml not found in zip", file=sys.stderr)
                sys.exit(1)
            print(f"[apple_health] Streaming {xml_names[0]} from zip...", file=sys.stderr)
            with z.open(xml_names[0]) as f:
                data = stream_parse(f)
    elif args.xml:
        with open(args.xml, "rb") as f:
            data = stream_parse(f)
    else:
        ap.print_help()
        sys.exit(1)

    summary = summarize(data)
    print(json.dumps(summary, indent=2, default=str))
    print("[apple_health] Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
