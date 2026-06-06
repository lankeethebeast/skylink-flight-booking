"""Time parsing and segment/layover enrichment utilities for flight data."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Sequence


def parse_time_to_minutes(value) -> Optional[int]:
    """Parse a time string into minutes since midnight.

    Supports:
      - ISO datetime: 2026-06-15T08:30:00 (with optional tz)
      - 24-hour: HH:MM, HH:MM:SS
      - 12-hour: H:MM am/pm, HH:MM am/pm, with optional seconds
    Returns ``None`` if unparseable.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    # ISO datetime: 2026-06-15T08:30:00 or with timezone
    if "T" in s:
        try:
            t = s.split("T", 1)[1]
            t = t.split("+", 1)[0].split("-", 1)[0].split("Z", 1)[0]
            parts = t.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return h * 60 + m
        except (ValueError, IndexError):
            return None
    # Look for 12-hour am/pm marker
    ampm_match: Optional[str] = None
    lower = s.lower()
    if "am" in lower or "pm" in lower:
        ampm_match = "am" if lower.endswith("am") else "pm"
        s_clean = lower.replace("am", "").replace("pm", "").strip()
    else:
        s_clean = s
    if ":" in s_clean:
        try:
            parts = s_clean.split(":")
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            if ampm_match:
                # Convert 12-hour to 24-hour
                if ampm_match == "am":
                    if h == 12:
                        h = 0
                else:  # pm
                    if h != 12:
                        h += 12
            return h * 60 + m
        except (ValueError, IndexError):
            return None
    return None


def format_minutes(total_minutes) -> str:
    """Format a minute count as '1h 30m' / '45m' / '2h'."""
    if total_minutes is None or total_minutes < 0:
        return ""
    h, m = divmod(int(total_minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    if h:
        return f"{h}h"
    return f"{m}m"


def compute_duration_str(dep_time, arr_time) -> str:
    """Compute a formatted duration string from departure and arrival times.

    Returns '' if either time is unparseable, otherwise a string like '1h 30m'.
    """
    dep_min = parse_time_to_minutes(dep_time)
    arr_min = parse_time_to_minutes(arr_time)
    if dep_min is None or arr_min is None:
        return ""
    diff = arr_min - dep_min
    if diff < 0:
        # crossed midnight
        diff += 24 * 60
    return format_minutes(diff)


def display_date(value) -> str:
    """Format visible dates as dd-mm-yyyy while preserving raw form/API values elsewhere.

    Supports ISO-like API/form dates such as:
      - 2026-06-15
      - 2026-06-15T08:30:00
    Falls back to the original value when it cannot be parsed.
    """
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")
    if not isinstance(value, str):
        return value

    raw = value.strip()
    date_part = raw.split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(date_part, fmt).strftime("%d-%m-%Y")
        except ValueError:
            continue
    return value


def _process_segments(segs: Sequence[dict]) -> List[Optional[dict]]:
    """Process a list of segments: set _seg_dur on each and return layovers list."""
    layovers: List[Optional[dict]] = []
    for idx, seg in enumerate(segs):
        if isinstance(seg, dict):
            seg_dep = seg.get("departure_time") or seg.get("dep_time")
            seg_arr = seg.get("arrival_time") or seg.get("arr_time")
            computed = compute_duration_str(seg_dep, seg_arr)
            existing_dur = seg.get("duration") or seg.get("duration_time")
            seg["_seg_dur"] = computed or (existing_dur or "")

        if idx >= len(segs) - 1:
            layovers.append(None)
            continue
        next_seg = segs[idx + 1]
        if not isinstance(seg, dict) or not isinstance(next_seg, dict):
            layovers.append(None)
            continue
        arr_min = parse_time_to_minutes(seg.get("arrival_time") or seg.get("arr_time"))
        next_dep_min = parse_time_to_minutes(
            next_seg.get("departure_time") or next_seg.get("dep_time")
        )
        layover_city = (
            seg.get("to")
            or seg.get("arrival_airport")
            or seg.get("destination")
            or ""
        )
        duration_str = ""
        duration_min = None
        if arr_min is not None and next_dep_min is not None:
            diff = next_dep_min - arr_min
            if diff < 0:
                diff += 24 * 60
            duration_min = diff
            duration_str = format_minutes(diff)
        layovers.append({
            "city": layover_city,
            "duration_str": duration_str,
            "duration_minutes": duration_min,
        })
    return layovers


def enrich_segments_with_layovers(flights) -> list:
    """For each flight, attach ``_layovers`` and inject per-segment duration.

    For each flight dict in ``flights``:
      - segments = flight["segments"][0]   (the inner array, outbound)
      - segments[1] (if present) is the return journey
      - sets ``seg["_seg_dur"]`` on every segment
      - sets ``flight["_layovers"]`` and ``flight["_return_layovers"]`` (lists parallel to segments)
      - sets ``flight["_computed_duration_outbound"]`` and ``flight["_computed_duration_return"]``
      - sets ``flight["_computed_duration"]`` (full journey, including layovers)
    """
    for f in flights or []:
        if not isinstance(f, dict):
            if isinstance(f, dict):
                f["_layovers"] = []
                f["_return_layovers"] = []
            continue
        segs_outer = f.get("segments") or []

        # Process outbound segments (segments[0])
        outbound_segs = segs_outer[0] if segs_outer and isinstance(segs_outer[0], list) else []
        layovers = _process_segments(outbound_segs)

        # Process return segments (segments[1]) if present
        return_segs = (
            segs_outer[1] if len(segs_outer) > 1 and isinstance(segs_outer[1], list) else []
        )
        return_layovers = _process_segments(return_segs)

        # Compute outbound duration
        if outbound_segs:
            out_dep = outbound_segs[0].get("departure_time") or outbound_segs[0].get("dep_time")
            out_arr = outbound_segs[-1].get("arrival_time") or outbound_segs[-1].get("arr_time")
            f["_computed_duration_outbound"] = compute_duration_str(out_dep, out_arr) or ""

        # Compute return duration
        if return_segs:
            ret_dep = return_segs[0].get("departure_time") or return_segs[0].get("dep_time")
            ret_arr = return_segs[-1].get("arrival_time") or return_segs[-1].get("arr_time")
            f["_computed_duration_return"] = compute_duration_str(ret_dep, ret_arr) or ""

        # Compute overall flight duration (must include layover time for multi-stop)
        computed = ""

        # 1) Try flight-level departure / arrival (often represents full journey)
        f_dep = f.get("departure_time") or f.get("dep_time")
        f_arr = f.get("arrival_time") or f.get("arr_time")
        if f_dep and f_arr:
            computed = compute_duration_str(f_dep, f_arr)

        # 2) Try first segment dep -> last segment arr (across all segment groups)
        all_segs = outbound_segs + return_segs
        if not computed and all_segs:
            first_dep = all_segs[0].get("departure_time") or all_segs[0].get("dep_time")
            last_arr = all_segs[-1].get("arrival_time") or all_segs[-1].get("arr_time")
            computed = compute_duration_str(first_dep, last_arr)

        # 3) Explicitly sum all segment durations + layover durations
        if not computed and all_segs:
            total_min = 0
            all_ok = True
            all_layovers = layovers + return_layovers
            for idx, seg in enumerate(all_segs):
                if not isinstance(seg, dict):
                    all_ok = False
                    break
                sd = seg.get("departure_time") or seg.get("dep_time")
                sa = seg.get("arrival_time") or seg.get("arr_time")
                sd_min = parse_time_to_minutes(sd)
                sa_min = parse_time_to_minutes(sa)
                if sd_min is None or sa_min is None:
                    all_ok = False
                    break
                seg_dur = sa_min - sd_min
                if seg_dur < 0:
                    seg_dur += 24 * 60
                total_min += seg_dur
                lo = all_layovers[idx] if idx < len(all_layovers) else None
                if lo and lo.get("duration_minutes") is not None:
                    total_min += lo["duration_minutes"]
            if all_ok and total_min > 0:
                computed = format_minutes(total_min)

        if computed:
            f["_computed_duration"] = computed

        f["_layovers"] = layovers
        f["_return_layovers"] = return_layovers
    return flights
