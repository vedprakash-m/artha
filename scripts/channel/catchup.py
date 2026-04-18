"""channel/catchup.py — Full catch-up orchestration via channel."""
from __future__ import annotations
import asyncio
import logging
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from channel.formatters import (
    _strip_frontmatter, _filter_noise_bullets, _trim_to_cap,
    _extract_section_summaries,
)
from channel.state_readers import (
    _read_state_file, _apply_scope_filter, _get_latest_briefing_path,
    _get_domain_open_items, _get_last_catchup_iso,
)
from channel.llm_bridge import _ask_llm, _ask_llm_ensemble, _gather_context, _detect_all_llm_clis, _call_single_llm
from channel.audit import _audit_log

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_BRIEFINGS_DIR = _ARTHA_DIR / "briefings"
_CONFIG_DIR = _ARTHA_DIR / "config"
_PROMPTS_DIR = _ARTHA_DIR / "prompts"

_DOMAIN_STATE_FILES: dict[str, str] = {
    "immigration": "immigration.md.age",
    "finance": "finance.md.age",
    "kids": "kids.md",
    "health": "health.md.age",
    "travel": "travel.md",
    "home": "home.md",
    "shopping": "shopping.md",
    "goals": "goals.md",
    "vehicle": "vehicle.md.age",
    "estate": "estate.md.age",
    "insurance": "insurance.md.age",
    "calendar": "calendar.md",
    "comms": "comms.md",
    "social": "social.md",
    "learning": "learning.md",
    "boundary": "boundary.md",
    "employment": "employment.md",
}

log = logging.getLogger("channel_listener")

_CATCHUP_MAX_CONTEXT_CHARS = 80_000
_CATCHUP_TIMEOUT_SEC = 300  # catch-up pipeline + LLM synthesis can take longer



# ── _run_pipeline ───────────────────────────────────────────────

async def _run_pipeline(since_iso: str) -> tuple[str, int]:
    """Run pipeline.py to fetch new emails/calendar. Return (jsonl_output, record_count)."""
    pipeline_script = _ARTHA_DIR / "scripts" / "pipeline.py"
    python = sys.executable

    try:
        proc = await asyncio.create_subprocess_exec(
            python, str(pipeline_script), "--since", since_iso,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_ARTHA_DIR),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120
        )
        output = stdout.decode("utf-8", errors="replace")
        err_text = stderr.decode("utf-8", errors="replace")
        if err_text:
            log.info("[catch-up] pipeline stderr: %s", err_text[:500])
        record_count = len([l for l in output.splitlines() if l.strip()])
        return output, record_count
    except asyncio.TimeoutError:
        log.error("[catch-up] pipeline timed out")
        return "", 0
    except Exception as exc:
        log.error("[catch-up] pipeline failed: %s", exc)
        return "", 0


# ── _gather_all_context ─────────────────────────────────────────

def _gather_all_context(max_chars: int = _CATCHUP_MAX_CONTEXT_CHARS) -> str:
    """Gather context for ALL domains — broader than _gather_context()."""
    sections: list[str] = []
    budget = max_chars

    # Open items (all open)
    oi_content, _ = _read_state_file("open_items")
    if oi_content:
        oi_parts: list[str] = []
        lines = oi_content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("- id: OI-"):
                block_lines = [line]
                i += 1
                while i < len(lines):
                    bline = lines[i].strip()
                    if bline.startswith("- id: OI-") or (
                        not bline and i + 1 < len(lines)
                        and lines[i + 1].strip().startswith("- id:")
                    ):
                        break
                    block_lines.append(lines[i])
                    i += 1
                block_text = "\n".join(block_lines)
                if "status: open" in block_text:
                    oi_parts.append(block_text)
                continue
            i += 1
        oi_text = "\n\n".join(oi_parts)
        if oi_text:
            sections.append(f"[Open Items]\n{oi_text}")
            budget -= len(oi_text) + 20

    # Goals
    goals_content, _ = _read_state_file("goals")
    if goals_content:
        goals_content = _strip_frontmatter(goals_content)
        cap = min(3000, budget // 8)
        if len(goals_content) > cap:
            goals_content = goals_content[:cap] + "\n…[truncated]"
        sections.append(f"[Goals]\n{goals_content}")
        budget -= len(goals_content) + 20

    # Calendar
    cal_content, _ = _read_state_file("calendar")
    if cal_content:
        cal_content = _strip_frontmatter(cal_content)
        cap = min(3000, budget // 8)
        if len(cal_content) > cap:
            cal_content = cal_content[:cap] + "\n…[truncated]"
        sections.append(f"[Calendar]\n{cal_content}")
        budget -= len(cal_content) + 20

    # All readable state files
    all_domains = sorted(_DOMAIN_STATE_FILES.keys())
    per_domain_cap = max(1500, budget // max(len(all_domains), 1))
    for domain in all_domains:
        fname = _DOMAIN_STATE_FILES[domain]
        if fname.endswith(".age"):
            # Encrypted — try prompt file instead
            prompt_file = _PROMPTS_DIR / f"{domain}.md"
            if prompt_file.exists():
                try:
                    ptxt = prompt_file.read_text(encoding="utf-8", errors="replace")
                    ptxt = _strip_frontmatter(ptxt)
                    cap = min(2000, per_domain_cap)
                    if len(ptxt) > cap:
                        ptxt = ptxt[:cap] + "\n…[truncated]"
                    sections.append(f"[Domain Prompt: {domain}]\n{ptxt}")
                    budget -= len(ptxt) + 30
                except OSError:
                    pass
            continue
        fpath = _STATE_DIR / fname
        if not fpath.exists():
            continue
        try:
            raw = fpath.read_text(encoding="utf-8", errors="replace")
            raw = _strip_frontmatter(raw)
            cap = min(per_domain_cap, budget // 4)
            if len(raw) > cap:
                raw = raw[:cap] + "\n…[truncated]"
            sections.append(f"[State: {domain}]\n{raw}")
            budget -= len(raw) + 20
        except OSError:
            pass

    return "\n\n".join(sections)


# ── _read_briefing_template ─────────────────────────────────────

def _read_briefing_template() -> str:
    """Read the standard briefing format template."""
    bf_path = _ARTHA_DIR / "config" / "briefing-formats.md"
    if bf_path.exists():
        try:
            txt = bf_path.read_text(encoding="utf-8", errors="replace")
            # Just the standard template section (first ~60 lines)
            lines = txt.splitlines()[:80]
            return "\n".join(lines)
        except OSError:
            pass
    return ""


# ── _save_briefing ──────────────────────────────────────────────

def _save_briefing(text: str) -> Path:
    """Save briefing to briefings/YYYY-MM-DD.md via canonical archive helper."""
    from lib.briefing_archive import save as _archive_save  # noqa: PLC0415
    result = _archive_save(
        text,
        source="telegram",
        subject="Artha Telegram Catch-Up",
    )
    today = datetime.now().strftime("%Y-%m-%d")
    return _BRIEFINGS_DIR / f"{today}.md"


# ── cmd_catchup ─────────────────────────────────────────────────

async def cmd_catchup(args: list[str], scope: str) -> tuple[str, str]:
    """Run a catch-up: fetch new data → LLM synthesis → briefing."""

    # Bridge result ingestion: ingest any Windows-executed results before
    # building the briefing, so the catch-up reflects latest action outcomes.
    # Runs only when bridge is enabled and this machine is the proposer role.
    # Spec: dual-setup.md §4.2 — "before briefing_adapter.py is invoked"
    try:
        from lib.config_loader import load_config as _br_load_config  # noqa: PLC0415
        _br_artha_cfg = _br_load_config("artha_config")
        if _br_artha_cfg.get("multi_machine", {}).get("bridge_enabled", False):
            from action_bridge import (  # noqa: PLC0415
                detect_role, get_bridge_dir, ingest_results, gc,
            )
            _br_ch_cfg: dict = _br_load_config("channels")
            if detect_role(_br_ch_cfg) == "proposer":
                    _br_dir = get_bridge_dir(_ARTHA_DIR)
                    from action_queue import ActionQueue as _BrAQ  # noqa: PLC0415
                    _br_queue = _BrAQ(_ARTHA_DIR)
                    ingest_results(_br_dir, _br_queue, _ARTHA_DIR)
                    gc(_br_dir, _ARTHA_DIR)
                    log.info("[bridge] Result ingestion complete (catch-up pre-step)")
    except Exception as _br_exc:
        log.warning("[bridge] catch-up result ingestion failed (non-fatal): %s", _br_exc)

    clis = _detect_all_llm_clis()
    if not clis:
        return "No LLM CLI available (install gemini, copilot, or claude).", "N/A"

    # Step 1: Determine since timestamp
    since_iso = _get_last_catchup_iso()
    log.info("[catch-up] Starting. since=%s", since_iso)

    # Step 2: Run pipeline to fetch new data
    jsonl_output, record_count = await _run_pipeline(since_iso)
    log.info("[catch-up] Pipeline returned %d records", record_count)

    # Step 3: Gather all domain context
    context = _gather_all_context()
    log.info("[catch-up] Context gathered: %d chars", len(context))

    # Step 4: Read briefing template
    template = _read_briefing_template()

    # Step 5: Build mega-prompt
    today_str = datetime.now().strftime("%A, %B %d, %Y")
    system_prompt = (
        "You are Artha, a personal intelligence assistant. "
        "Produce a catch-up briefing following the template format below. "
        "Use ONLY the data provided — never fabricate. "
        f"Today is {today_str}. Last catch-up: {since_iso}.\n"
        "Do NOT use markdown formatting — use plain text with Unicode box-drawing "
        "characters (━) for section dividers as shown in the template.\n"
        "Keep it concise: max 3 bullets per domain, skip domains with no new activity "
        "(but state 'No new activity' for major domains).\n"
        "Include ONE THING with Urgency×Impact×Agency scoring.\n"
        "If new emails/events are provided, incorporate them. "
        "If none, synthesize from current state files and open items."
    )

    parts = [system_prompt, f"\n--- BRIEFING TEMPLATE ---\n{template}"]
    if jsonl_output.strip():
        # Cap new data to avoid blowing context
        new_data = jsonl_output[:20_000]
        parts.append(f"\n--- NEW EMAILS/EVENTS ({record_count} records) ---\n{new_data}")
    parts.append(f"\n--- CURRENT STATE & OPEN ITEMS ---\n{context}")

    full_prompt = "\n".join(parts)
    # Trim total to stay within limits
    if len(full_prompt) > _CATCHUP_MAX_CONTEXT_CHARS:
        full_prompt = full_prompt[:_CATCHUP_MAX_CONTEXT_CHARS]

    log.info("[catch-up] Prompt size: %d chars. Calling LLM...", len(full_prompt))

    # Step 6: Call LLM with failover
    briefing = ""
    for name, executable, base_args in clis:
        try:
            briefing = await _call_single_llm(
                name, executable, base_args, full_prompt,
                "Produce the catch-up briefing now.",
                timeout=_CATCHUP_TIMEOUT_SEC,
            )
        except Exception as _llm_exc:  # LLMUnavailableError or unexpected
            log.warning("[catch-up] %s unavailable (%s), trying next CLI...", name, _llm_exc)
            continue
        if briefing:
            log.info("[catch-up] Briefing produced by %s (%d chars)", name, len(briefing))
            break
        log.warning("[catch-up] %s failed, trying next CLI...", name)

    if not briefing:
        return "Catch-up produced empty output. Try again.", "N/A"

    # Strip Claude narration preamble (e.g. "Now I have all the data...") that
    # appears before the actual formatted briefing (which starts with ━━━).
    if "\u2501\u2501" in briefing:
        idx = briefing.find("\u2501\u2501")
        briefing = briefing[idx:]

    # Step 7: Save briefing
    saved_path = _save_briefing(briefing)
    log.info("[catch-up] Briefing saved to %s (%d chars)", saved_path.name, len(briefing))

    _audit_log(
        "CHANNEL_CATCHUP",
        emails_fetched=record_count,
        briefing_chars=len(briefing),
        saved_to=saved_path.name,
    )

    return briefing, "N/A"
