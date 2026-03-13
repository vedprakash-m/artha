#!/usr/bin/env python3
"""
canvas_fetch.py — Fetch assignment/grade data from Canvas LMS.

Auth: Canvas API tokens stored in system keychain per child (keys from user_profile.yaml).
      Run once: python3 canvas_fetch.py --setup
Output: Updates state/kids.md grades section for each configured child.
Health check: python3 canvas_fetch.py --health

Ref: PRD F4.10, TS §7.18, T-2.2.5
"""

import argparse
import json
import os
import re
import sys
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Cross-platform venv re-exec ───────────────────────────────────────────────
ARTHA_ROOT = Path(__file__).parent.parent.resolve()
VENV_PY_MAC = Path.home() / ".artha-venvs" / ".venv" / "bin" / "python3"
VENV_PY_WIN = Path.home() / ".artha-venvs" / ".venv-win" / "Scripts" / "python.exe"

def _reexec_in_venv() -> None:
    """Re-exec script inside the project venv if not already there."""
    in_venv = (
        hasattr(sys, "real_prefix")
        or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
    )
    if not in_venv:
        venv_py = VENV_PY_WIN if sys.platform == "win32" else VENV_PY_MAC
        if venv_py.exists():
            os.execv(str(venv_py), [str(venv_py)] + sys.argv)

_reexec_in_venv()

try:
    import requests
    import keyring
except ImportError as e:
    print(f"❌ Missing dependency: {e}. Run: pip install requests keyring", file=sys.stderr)
    sys.exit(1)

# ── Constants (profile-derived) ───────────────────────────────────────────────────────────────────
def _build_canvas_config() -> tuple[str, dict]:
    """Build (base_url, students_dict) from user_profile.yaml if available."""
    try:
        from profile_loader import children, has_profile
    except ImportError:
        return "", {}  # profile_loader not available in this context

    if not has_profile():
        return "", {}

    base_url = ""
    students: dict[str, dict] = {}
    for child in children():
        name = child.get("name", "")
        school = child.get("school", {}) or {}
        url = school.get("canvas_url", "")
        key = school.get("canvas_keychain_key", "")
        if name and url and key:
            if not base_url:
                base_url = url
            students[name] = {"key": key}
    return base_url, students

_CANVAS_BASE_URL, _STUDENTS_FROM_PROFILE = _build_canvas_config()

CANVAS_BASE_URL = _CANVAS_BASE_URL  # e.g. "https://yourdistrict.instructure.com"
STUDENTS = _STUDENTS_FROM_PROFILE   # {"ChildName": {"key": "artha-canvas-token-childname"}, ...}

KIDS_STATE = ARTHA_ROOT / "state" / "kids.md"
SERVICE_NAME = "artha-canvas"
TOKEN_DIR = Path.home() / ".artha-tokens"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_token(student_name: str) -> str | None:
    key = STUDENTS[student_name]["key"]
    # Try keychain first
    try:
        token = keyring.get_password("artha-canvas", key)
        if token:
            return token
    except Exception:
        pass
    # Fall back to token file
    token_file = TOKEN_DIR / f"canvas-token-{student_name.lower()}.json"
    if token_file.exists():
        with open(token_file) as f:
            return json.load(f).get("token")
    return None


def _canvas_get(token: str, endpoint: str, params: dict | None = None) -> list | dict:
    """Paginated GET against Canvas REST API. Returns all pages combined."""
    url = f"{CANVAS_BASE_URL}/api/v1{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            results.extend(data)
        else:
            return data
        # Check for next page link
        link_header = resp.headers.get("Link", "")
        next_url = None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                match = re.search(r"<([^>]+)>", part)
                if match:
                    next_url = match.group(1)
                    break
        url = next_url
        params = None  # params already included in next_url
    return results


def _letter_grade(score: float | None, points_possible: float | None) -> str:
    if score is None or not points_possible:
        return "N/A"
    pct = (score / points_possible) * 100
    if pct >= 93:
        return "A"
    elif pct >= 90:
        return "A-"
    elif pct >= 87:
        return "B+"
    elif pct >= 83:
        return "B"
    elif pct >= 80:
        return "B-"
    elif pct >= 77:
        return "C+"
    elif pct >= 73:
        return "C"
    elif pct >= 70:
        return "C-"
    elif pct >= 60:
        return "D"
    else:
        return "F"


def _gpa_point(letter: str) -> float:
    table = {
        "A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7,
        "C+": 2.3, "C": 2.0, "C-": 1.7, "D": 1.0, "F": 0.0, "N/A": 0.0,
    }
    return table.get(letter, 0.0)

# ── Core fetch logic ──────────────────────────────────────────────────────────

def fetch_student_data(student_name: str, token: str) -> dict:
    """Fetch courses, recent assignments and grades for one student."""
    now = datetime.now(timezone.utc)

    # Active courses this term
    courses = _canvas_get(token, "/courses", {
        "enrollment_state": "active",
        "include[]": ["term", "total_scores"],
        "per_page": 50,
    })

    student_data = {
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
        course_info = {
            "id": course["id"],
            "name": course["name"],
            "current_score": course.get("enrollments", [{}])[0].get("computed_current_score"),
            "final_score": course.get("enrollments", [{}])[0].get("computed_final_score"),
        }
        if course_info["current_score"] is not None:
            course_info["letter_grade"] = _letter_grade(
                course_info["current_score"], 100
            )
        student_data["courses"].append(course_info)
        course_ids.append(course["id"])

    # Upcoming assignments (due in next 14 days)
    cutoff = (now + timedelta(days=14)).isoformat()
    for cid in course_ids[:10]:  # cap at 10 courses to avoid quota
        try:
            assignments = _canvas_get(token, f"/courses/{cid}/assignments", {
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
            continue  # skip course on error

    # Sort upcoming by due date
    student_data["upcoming_assignments"].sort(key=lambda x: x["due_at"] or "")

    # Recent grades (last 7 days)
    week_ago = (now - timedelta(days=7)).isoformat()
    for cid in course_ids[:10]:
        try:
            submissions = _canvas_get(token, f"/courses/{cid}/students/submissions", {
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

    # Calculate weighted GPA estimate from course scores
    scored_courses = [
        c for c in student_data["courses"] if c.get("current_score") is not None
    ]
    if scored_courses:
        letters = [_letter_grade(c["current_score"], 100) for c in scored_courses]
        gpa = sum(_gpa_point(l) for l in letters) / len(letters)
        student_data["estimated_gpa"] = round(gpa, 2)
    else:
        student_data["estimated_gpa"] = None

    return student_data

# ── State file update ─────────────────────────────────────────────────────────

def _update_kids_md(all_students: list[dict]) -> None:
    """Update state/kids.md with the latest Canvas data block."""
    if not KIDS_STATE.exists():
        print(f"⚠️  {KIDS_STATE} not found — creating Canvas section standalone", file=sys.stderr)
        KIDS_STATE.write_text("---\ndomain: kids\nlast_updated: \n---\n")

    content = KIDS_STATE.read_text(encoding="utf-8")

    # Build replacement Canvas block
    lines = ["\n## Canvas Academic Data\n", f"*Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n"]

    for sd in all_students:
        lines.append(f"### {sd['name']}\n")
        if sd.get("estimated_gpa") is not None:
            lines.append(f"**Estimated GPA:** {sd['estimated_gpa']:.2f} (unweighted)\n\n")

        if sd["courses"]:
            lines.append("**Current Course Scores:**\n")
            for c in sd["courses"]:
                score = c.get("current_score")
                letter = c.get("letter_grade", "N/A")
                score_str = f"{score:.1f}%" if score is not None else "not reported"
                lines.append(f"- {c['name']}: {score_str} ({letter})\n")
            lines.append("\n")

        if sd["upcoming_assignments"]:
            lines.append("**Upcoming Assignments (14 days):**\n")
            for a in sd["upcoming_assignments"][:8]:
                due_str = a["due_at"][:10] if a["due_at"] else "no due date"
                status = "✅ submitted" if a["submitted"] else "⏳ pending"
                lines.append(f"- [{due_str}] {a['course_name']}: {a['name']} — {status}\n")
            lines.append("\n")

        if sd["recent_grades"]:
            lines.append("**Recently Graded (7 days):**\n")
            for g in sd["recent_grades"][:5]:
                lines.append(
                    f"- {g['course_name']}: {g['assignment']} — "
                    f"{g['score']}/{g['points_possible']} ({g['letter']})\n"
                )
            lines.append("\n")

    canvas_block = "".join(lines)

    # Replace existing Canvas block or append
    pattern = r"\n## Canvas Academic Data\n.*?(?=\n## |\Z)"
    if re.search(pattern, content, re.DOTALL):
        content = re.sub(pattern, canvas_block.rstrip("\n"), content, flags=re.DOTALL)
    else:
        content = content.rstrip("\n") + "\n" + canvas_block

    # Update last_updated in frontmatter
    content = re.sub(
        r"(last_updated:\s*).*",
        f"last_updated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        content,
        count=1,
    )

    KIDS_STATE.write_text(content, encoding="utf-8")
    print(f"✅ {KIDS_STATE} updated with Canvas data")

# ── Setup ─────────────────────────────────────────────────────────────────────

def setup_tokens() -> None:
    """Interactive setup to store Canvas API tokens in keychain."""
    print("Canvas LMS API Token Setup")
    print("━" * 40)
    print(f"Base URL: {CANVAS_BASE_URL}")
    print("\nTo get your Canvas API token:")
    print("  1. Log in to Canvas as the student")
    print("  2. Account → Settings → New Access Token")
    print("  3. Set purpose 'Artha', no expiry")
    print()
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    for student_name, cfg in STUDENTS.items():
        print(f"Enter Canvas API token for {student_name} (leave blank to skip):")
        import getpass
        token = getpass.getpass(f"  {student_name} token: ").strip()
        if token:
            try:
                keyring.set_password("artha-canvas", cfg["key"], token)
                print(f"  ✅ Token saved to keychain for {student_name}")
            except Exception as e:
                # Fall back to file
                token_file = TOKEN_DIR / f"canvas-token-{student_name.lower()}.json"
                token_file.write_text(json.dumps({"token": token}))
                token_file.chmod(0o600)
                print(f"  ✅ Token saved to {token_file} (keychain unavailable: {e})")
        else:
            print(f"  ⏭  Skipped {student_name}")
    print("\nSetup complete. Run `python3 canvas_fetch.py --health` to verify.")

# ── Health check ──────────────────────────────────────────────────────────────

def health_check() -> int:
    """Quick connectivity check. Returns 0 on success, 1 on failure."""
    for student_name in STUDENTS:
        token = _get_token(student_name)
        if not token:
            print(f"⚠️  Canvas: No token configured for {student_name} — run --setup")
            continue
        try:
            profile = _canvas_get(token, "/users/self/profile")
            name = profile.get("name", "unknown") if isinstance(profile, dict) else "unknown"
            print(f"✅ Canvas ({student_name}): connected — account: {name}")
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                print(f"❌ Canvas ({student_name}): token invalid (401) — re-run --setup")
                return 1
            else:
                print(f"❌ Canvas ({student_name}): HTTP {e.response.status_code}")
                return 1
        except Exception as e:
            print(f"❌ Canvas ({student_name}): {e}")
            return 1
    return 0

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Canvas LMS data fetcher for Artha")
    parser.add_argument("--health", action="store_true", help="Quick connectivity check")
    parser.add_argument("--setup", action="store_true", help="Interactive token setup")
    parser.add_argument("--student", choices=list(STUDENTS.keys()) + ["all"], default="all",
                        help="Which student to fetch (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not write state file")
    parser.add_argument("--output-json", action="store_true", help="Print JSON to stdout instead")
    args = parser.parse_args()

    if args.setup:
        setup_tokens()
        return

    if args.health:
        sys.exit(health_check())

    students_to_fetch = list(STUDENTS.keys()) if args.student == "all" else [args.student]
    all_data = []

    for student_name in students_to_fetch:
        token = _get_token(student_name)
        if not token:
            print(f"⚠️  No Canvas token for {student_name} — skipping. Run --setup to configure.",
                  file=sys.stderr)
            continue
        try:
            print(f"Fetching Canvas data for {student_name}...")
            data = fetch_student_data(student_name, token)
            courses_count = len(data["courses"])
            upcoming_count = len(data["upcoming_assignments"])
            gpa = data.get("estimated_gpa")
            gpa_str = f"{gpa:.2f}" if gpa is not None else "N/A"
            print(f"  {student_name}: {courses_count} courses · {upcoming_count} upcoming assignments · GPA ~{gpa_str}")
            all_data.append(data)
        except requests.HTTPError as e:
            print(f"❌ Canvas fetch failed for {student_name}: HTTP {e.response.status_code}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"❌ Canvas fetch failed for {student_name}: {e}", file=sys.stderr)
            sys.exit(1)

    if args.output_json:
        print(json.dumps(all_data, indent=2, default=str))
        return

    if not args.dry_run and all_data:
        _update_kids_md(all_data)
    elif args.dry_run:
        print("[dry-run] Would update state/kids.md")
        for d in all_data:
            print(json.dumps(d, indent=2, default=str))


if __name__ == "__main__":
    main()
