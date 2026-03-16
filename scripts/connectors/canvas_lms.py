"""
scripts/connectors/canvas_lms.py — Canvas LMS connector (standalone).

Fetches student course/assignment/grade data from the Canvas LMS API and
yields standardised records. All API logic is self-contained — no dependency
on the legacy canvas_fetch.py script.

Handler contract: implements fetch() and health_check() per connectors/base.py.

Ref: supercharge-reloaded.md §1.4
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Helpers (moved from canvas_fetch.py)
# ---------------------------------------------------------------------------

def _build_canvas_config() -> tuple[str, dict]:
    """Return (base_url, students_dict) from user_profile.yaml."""
    try:
        from profile_loader import children, has_profile  # type: ignore[import]
    except ImportError:
        return "", {}
    if not has_profile():
        return "", {}
    base_url = ""
    students: dict[str, dict] = {}
    for child in children():
        name = child.get("name", "")
        school = child.get("school") or {}
        url = school.get("canvas_url", "")
        key = school.get("canvas_keychain_key", "")
        if name and url and key:
            if not base_url:
                base_url = url
            students[name] = {"key": key}
    return base_url, students


def _get_token(student_name: str, key: str) -> Optional[str]:
    """Get Canvas API token from keyring or token file."""
    try:
        import keyring  # type: ignore[import]
        token = keyring.get_password("artha-canvas", key)
        if token:
            return token
    except Exception:
        pass
    from pathlib import Path
    import json as _json
    token_file = Path.home() / ".artha-tokens" / f"canvas-token-{student_name.lower()}.json"
    if token_file.exists():
        with open(token_file) as f:
            return _json.load(f).get("token")
    return None


def _canvas_get(canvas_url: str, token: str, endpoint: str, params: Optional[dict] = None) -> list:
    """Paginated GET against Canvas REST API. Returns all pages combined."""
    import requests  # type: ignore[import]
    url = f"{canvas_url}/api/v1{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    results: list = []
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            results.extend(data)
        else:
            return [data]
        link_header = resp.headers.get("Link", "")
        next_url = None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<([^>]+)>", part)
                if m:
                    next_url = m.group(1)
                    break
        url = next_url
        params = None
    return results


def _letter_grade(score: Optional[float], points_possible: Optional[float]) -> str:
    if score is None or not points_possible:
        return "N/A"
    pct = (score / points_possible) * 100
    if pct >= 93: return "A"
    if pct >= 90: return "A-"
    if pct >= 87: return "B+"
    if pct >= 83: return "B"
    if pct >= 80: return "B-"
    if pct >= 77: return "C+"
    if pct >= 73: return "C"
    if pct >= 70: return "C-"
    if pct >= 60: return "D"
    return "F"


def _gpa_point(letter: str) -> float:
    return {
        "A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7,
        "C+": 2.3, "C": 2.0, "C-": 1.7, "D": 1.0, "F": 0.0, "N/A": 0.0,
    }.get(letter, 0.0)


def _fetch_student_data(canvas_url: str, student_name: str, token: str) -> dict:
    """Fetch courses, recent assignments and grades for one student."""
    now = datetime.now(timezone.utc)
    courses = _canvas_get(canvas_url, token, "/courses", {
        "enrollment_state": "active",
        "include[]": ["term", "total_scores"],
        "per_page": 50,
    })
    student_data: dict = {
        "name": student_name,
        "fetched_at": now.isoformat(),
        "courses": [],
        "upcoming_assignments": [],
        "recent_grades": [],
    }
    course_ids = []
    for course in courses:
        if not course.get("name") or course.get("access_restricted_by_date"):
            continue
        enrollments = course.get("enrollments") or [{}]
        course_info = {
            "id": course["id"],
            "name": course["name"],
            "current_score": enrollments[0].get("computed_current_score"),
            "final_score": enrollments[0].get("computed_final_score"),
        }
        if course_info["current_score"] is not None:
            course_info["letter_grade"] = _letter_grade(course_info["current_score"], 100)
        student_data["courses"].append(course_info)
        course_ids.append(course["id"])
    cutoff = (now + timedelta(days=14)).isoformat()
    for cid in course_ids[:10]:
        try:
            assignments = _canvas_get(canvas_url, token, f"/courses/{cid}/assignments", {
                "bucket": "upcoming",
                "per_page": 20,
                "include[]": ["submission"],
            })
            for a in assignments:
                due = a.get("due_at")
                if due and due <= cutoff:
                    sub = a.get("submission") or {}
                    student_data["upcoming_assignments"].append({
                        "course_id": cid,
                        "course_name": next(
                            (c["name"] for c in student_data["courses"] if c["id"] == cid), ""
                        ),
                        "name": a.get("name", ""),
                        "due_at": due,
                        "points_possible": a.get("points_possible"),
                        "submitted": sub.get("submitted_at") is not None,
                        "score": sub.get("score"),
                    })
        except Exception:
            continue
    student_data["upcoming_assignments"].sort(key=lambda x: x["due_at"] or "")
    week_ago = (now - timedelta(days=7)).isoformat()
    for cid in course_ids[:10]:
        try:
            submissions = _canvas_get(canvas_url, token, f"/courses/{cid}/students/submissions", {
                "student_ids[]": ["self"],
                "submitted_since": week_ago,
                "include[]": ["assignment"],
                "per_page": 20,
            })
            for sub in submissions:
                if sub.get("score") is None:
                    continue
                assignment = sub.get("assignment") or {}
                pp = assignment.get("points_possible") or 1
                letter = _letter_grade(sub["score"], pp)
                student_data["recent_grades"].append({
                    "course_name": next(
                        (c["name"] for c in student_data["courses"] if c["id"] == cid), ""
                    ),
                    "assignment": assignment.get("name", ""),
                    "score": sub["score"],
                    "points_possible": pp,
                    "letter": letter,
                    "graded_at": sub.get("graded_at", ""),
                })
        except Exception:
            continue
    scored_courses = [c for c in student_data["courses"] if c.get("current_score") is not None]
    if scored_courses:
        letters = [_letter_grade(c["current_score"], 100) for c in scored_courses]
        gpa = sum(_gpa_point(l) for l in letters) / len(letters)
        student_data["estimated_gpa"] = round(gpa, 2)
    else:
        student_data["estimated_gpa"] = None
    return student_data


# ---------------------------------------------------------------------------
# Public handler interface
# ---------------------------------------------------------------------------

def fetch(
    *,
    since: str,
    max_results: int = 50,
    auth_context: Dict[str, Any],
    source_tag: str = "canvas_lms",
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield one student-data record per configured Canvas student."""
    canvas_url, students_cfg = _build_canvas_config()
    if not canvas_url or not students_cfg:
        print("[canvas_lms] No Canvas students configured in user_profile.yaml", file=sys.stderr)
        return

    for student_name, cfg in students_cfg.items():
        token = (
            auth_context.get("tokens", {}).get(student_name)
            or _get_token(student_name, cfg.get("key", ""))
        )
        if not token:
            print(f"[canvas_lms] Skipping '{student_name}' — no API token", file=sys.stderr)
            continue
        try:
            data = _fetch_student_data(canvas_url, student_name, token)
            if source_tag:
                data["source"] = source_tag
            yield data
        except Exception as exc:
            print(f"[canvas_lms] Error fetching '{student_name}': {exc}", file=sys.stderr)


def health_check(auth_context: Dict[str, Any]) -> bool:
    """Verify Canvas tokens are present and at least one student is reachable."""
    canvas_url, students_cfg = _build_canvas_config()
    if not canvas_url or not students_cfg:
        print("[canvas_lms] health_check: no Canvas students configured", file=sys.stderr)
        return False
    for student_name, cfg in students_cfg.items():
        token = (
            auth_context.get("tokens", {}).get(student_name)
            or _get_token(student_name, cfg.get("key", ""))
        )
        if not token:
            continue
        try:
            _canvas_get(canvas_url, token, "/users/self/profile")
            return True
        except Exception:
            continue
    print("[canvas_lms] health_check: no reachable Canvas tokens", file=sys.stderr)
    return False
