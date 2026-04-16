"""channel/llm_bridge.py — LLM CLI abstraction, failover, ensemble."""
from __future__ import annotations
import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from channel.state_readers import _read_state_file, _apply_scope_filter, _DOMAIN_TO_STATE_FILE
from channel.formatters import _trim_to_cap, _extract_section_summaries, _strip_frontmatter
from channel.audit import _audit_log

try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from lib.error_messages import get_message as _get_error_message
except ImportError:
    def _get_error_message(code: str) -> str:  # type: ignore[misc]
        return f"Error: {code} — check your connector configuration."

try:
    from lib.exceptions import LLMUnavailableError  # RD-51
except ImportError:
    class LLMUnavailableError(RuntimeError):  # type: ignore[no-redef]
        """Fallback stub when exceptions.py is not on path."""
        def __init__(self, reason: str, last_exit_code: int = -1) -> None:
            super().__init__(f"LLM unavailable: {reason} (exit {last_exit_code})")
            self.reason = reason
            self.last_exit_code = last_exit_code

_ARTHA_DIR = Path(__file__).resolve().parents[2]
_STATE_DIR = _ARTHA_DIR / "state"
_PROMPTS_DIR = _ARTHA_DIR / "prompts"

log = logging.getLogger("channel_listener")

_LLM_TIMEOUT_SEC = 90
_LLM_MAX_CONTEXT_CHARS = 30_000
_CATCHUP_TIMEOUT_SEC = 300
_CATCHUP_MAX_CONTEXT_CHARS = 80_000

_QUESTION_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "health": ["health", "doctor", "medication", "appointment", "dental",
               "vision", "prescription", "symptom"],
    "finance": ["money", "spend", "budget", "investment", "stock",
                "account", "bank", "transfer", "bill", "payment"],
    "immigration": ["visa", "ead", "green card", "uscis", "i-485", "i-765",
                    "priority date", "perm"],
    "goals": ["goal", "objective", "milestone", "progress", "okr", "target"],
    "kids": ["kid", "school", "homework", "grade", "teacher", "lunch",
              "extracurricular", "pickup"],
    "home": ["home", "house", "repair", "maintenance", "mortgage", "hoa",
              "utilities"],
    "calendar": ["calendar", "schedule", "appointment", "meeting", "event"],
    "employment": ["job", "microsoft", "work", "salary", "manager", "career",
                   "team", "promotion", "review"],
    "shopping": ["shopping", "buy", "purchase", "order", "amazon", "costco"],
    "social": ["social", "friend", "family", "contact", "birthday", "reunion"],
    "learning": ["learn", "course", "book", "study", "certification"],
}

# Domains that require vault decryption — blocked on bridge/Telegram path.
_ENCRYPTED_DOMAINS: frozenset[str] = frozenset({
    "finance", "immigration", "health", "insurance", "estate",
    "vehicle", "caregiving",
})

# Intents that touch encrypted domains (used for bridge-path restriction).
_INTENT_ENCRYPTED_DOMAINS: dict[str, list[str]] = {
    "immigration-query": ["immigration"],
}

_INTENT_PATTERNS: dict[str, list[str]] = {
    # Core seven
    "brief": [
        r"catch me up", r"morning briefing", r"sitrep",
        r"what did i miss", r"brief me",
    ],
    "work": [
        r"work briefing", r"what'?s happening at work",
        r"work update", r"work catch.?up",
    ],
    "items": [
        r"what'?s open", r"open items", r"show.*items",
        r"what'?s overdue", r"what'?s due",
    ],
    "goals": [
        r"how are my goals", r"goal pulse", r"show.*goals",
        r"goal progress",
    ],
    "status": [
        r"how'?s everything", r"quick status", r"artha status",
    ],
    # High-value sub-commands
    "work-prep": [
        r"prep me for", r"prepare for my meeting",
        r"meeting prep", r"ready for my",
        r"what should i know before",
    ],
    "work-sprint": [
        r"sprint health", r"delivery health", r"how'?s the sprint",
    ],
    "work-connect-prep": [
        r"prepare for.*connect", r"connect review",
        r"review prep", r"calibration",
    ],
    "content-draft": [
        r"write a.*post", r"draft.*linkedin", r"draft.*post",
        r"write.*linkedin",
    ],
    "items-done": [
        r"mark.*done", r"complete.*item", r"finished.*item",
        r"done with",
    ],
    "items-quick": [
        r"anything quick", r"quick wins", r"what can i knock out",
        r"5.?min.*tasks?",
    ],
    # Starter set: immigration; expand health/finance patterns once validated.
    "immigration-query": [
        r"visa status", r"immigration", r"\bead\b",
        r"green card", r"priority date",
    ],
    "teach": [
        r"explain\b", r"teach me", r"what is.*\?",
        r"what does.*mean",
    ],
    "dashboard": [
        r"show.*everything", r"full.*dashboard", r"life dashboard",
        r"big picture",
    ],
    "radar": [
        r"what'?s new in ai", r"ai trends?", r"show radar",
        r"ai news",
    ],
    "reconnect": [
        r"reconnect gmail", r"reconnect google",
        r"reconnect outlook", r"reconnect microsoft",
        r"fix encryption", r"reconnect icloud",
        r"reconnect workiq", r"reconnect work.?iq",
        r"fix.*connection", r"reauth",
    ],
}


# ── _classify_intent ────────────────────────────────────────────

def _classify_intent(question: str) -> str | None:
    """Return the first matching intent key for a question, or None."""
    q = question.lower().strip()
    for intent, patterns in _INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, q):
                return intent
    return None


# ── _fuzzy_resolve_item ─────────────────────────────────────────

def _fuzzy_resolve_item(description: str, items_text: str) -> str | None:
    """Find the best matching OI-NNN for a natural language description.

    Scores each item by word overlap with the description.
    Returns the OI-NNN when a single clear winner exists (score ≥ 2 and
    strictly greater than second-best), or None if ambiguous.
    """
    desc_words = {w for w in description.lower().split() if len(w) > 3}
    if not desc_words:
        return None

    best_id: str | None = None
    best_score = 0
    second_best_score = 0

    for m in re.finditer(
        r'- id:\s*(OI-\d+)(.*?)(?=\n- id:\s*OI-|\Z)', items_text, re.DOTALL
    ):
        item_id = m.group(1)
        item_body = m.group(2).lower()
        score = sum(1 for w in desc_words if w in item_body)
        if score > best_score:
            second_best_score = best_score
            best_score = score
            best_id = item_id
        elif score > second_best_score:
            second_best_score = score

    if best_score >= 2 and best_score > second_best_score:
        return best_id
    return None


# ── _gather_intent_context ──────────────────────────────────────

def _gather_intent_context(
    intent: str,
    max_chars: int = _CATCHUP_MAX_CONTEXT_CHARS,
) -> str:
    """Gather structured context files for a classified intent.

    Returns richer, pipeline-equivalent context instead of the generic
    keyword domain detection used by the fallback path.
    """
    sections: list[str] = []

    def _read_safe(path: Path) -> str:
        try:
            return _strip_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return ""

    if intent in ("brief", "dashboard", "status"):
        for fname in ("goals.md", "open_items.md", "calendar.md"):
            sf = _STATE_DIR / fname
            if sf.exists():
                sections.append(f"[{fname}]\n{_read_safe(sf)}")
        work_dir = _STATE_DIR / "work"
        for fname in ("work_status.md", "work_projects.md"):
            sf = work_dir / fname
            if sf.exists():
                sections.append(f"[work/{fname}]\n{_read_safe(sf)}")

    elif intent in ("work", "work-prep", "work-sprint", "work-connect-prep"):
        work_dir = _STATE_DIR / "work"
        work_files: dict[str, list[Path]] = {
            "work": list(work_dir.glob("*.md")) if work_dir.exists() else [],
            "work-prep": [
                work_dir / "work_calendar.md", work_dir / "work_people.md",
                work_dir / "work_comms.md", work_dir / "work_notes.md",
            ],
            "work-sprint": [
                work_dir / "work_projects.md", work_dir / "work_goals.md",
                work_dir / "work_performance.md",
            ],
            "work-connect-prep": [
                work_dir / "work_goals.md", work_dir / "work_performance.md",
                work_dir / "work_comms.md", work_dir / "work_calendar.md",
            ],
        }
        for sf in work_files.get(intent, []):
            if sf.exists():
                sections.append(f"[{sf.name}]\n{_read_safe(sf)}")

    elif intent in ("items", "items-quick", "items-done"):
        oi_content, _ = _read_state_file("open_items")
        if oi_content:
            sections.append(f"[open_items.md]\n{oi_content}")

    elif intent == "goals":
        goals_content, _ = _read_state_file("goals")
        if goals_content:
            sections.append(f"[goals.md]\n{goals_content}")

    elif intent == "immigration-query":
        pfile = _PROMPTS_DIR / "immigration.md"
        if pfile.exists():
            sections.append(f"[prompts/immigration.md]\n{_read_safe(pfile)}")
        state_content, _ = _read_state_file("immigration")
        if state_content:
            sections.append(f"[state/immigration.md]\n{state_content}")

    elif intent == "content-draft":
        pr_content, _ = _read_state_file("pr_manager")
        if pr_content:
            sections.append(f"[state/pr_manager.md]\n{pr_content}")
        gallery_file = _STATE_DIR / "gallery.yaml"
        if gallery_file.exists():
            sections.append(f"[state/gallery.yaml]\n{_read_safe(gallery_file)}")

    elif intent == "radar":
        for fname in ("ai_trend_radar.md", "digital.md"):
            sf = _STATE_DIR / fname
            if sf.exists():
                sections.append(f"[state/{fname}]\n{_read_safe(sf)}")

    elif intent == "reconnect":
        # Include health-check.md so the LLM knows which connectors are failing.
        hc = _STATE_DIR / "health-check.md"
        if hc.exists():
            sections.append(f"[state/health-check.md]\n{_read_safe(hc)}")
        # Static reconnect guide — no vault access needed.
        sections.append(
            "[reconnect-guide]\n"
            "To reconnect a service, run the appropriate command from the Artha directory:\n"
            "• Gmail / Google Calendar: python scripts/setup_google_oauth.py\n"
            "• Outlook / Microsoft 365: python scripts/setup_msgraph_oauth.py\n"
            "• iCloud: python scripts/setup_icloud_auth.py\n"
            "• WorkIQ: npx workiq login\n"
            "• Encryption (fix or set up): python scripts/lib/auto_vault.py\n"
            "Note: These require terminal (CLI) access. "
            "If you are on the bridge/Telegram path, open your terminal first."
        )

    # intent == "teach" → no state files needed; LLM uses its knowledge directly.

    context = "\n\n".join(sections)
    if len(context) > max_chars:
        context = context[:max_chars] + "\n…[context truncated]"
    return context



# ── _detect_domains ─────────────────────────────────────────────

def _detect_domains(question: str) -> list[str]:
    """Return list of relevant domain names for a question."""
    q_lower = question.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _QUESTION_DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in q_lower:
                scores[domain] = scores.get(domain, 0) + 1
    if not scores:
        return ["general"]
    # Return top 3 domains by keyword hits
    return [d for d, _ in sorted(scores.items(), key=lambda x: -x[1])][:3]


# ── _gather_context ─────────────────────────────────────────────

def _gather_context(domains: list[str], max_chars: int = _LLM_MAX_CONTEXT_CHARS) -> str:
    """Gather relevant context from prompts/ and state/ for the given domains."""
    sections: list[str] = []
    budget = max_chars

    # Always include open items (open status only, compact)
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
                    if bline.startswith("- id: OI-") or (not bline and i + 1 < len(lines) and lines[i + 1].strip().startswith("- id:")):
                        break
                    block_lines.append(lines[i])
                    i += 1
                block_text = "\n".join(block_lines)
                # Only include open items
                if "status: open" in block_text:
                    oi_parts.append(block_text)
                continue
            i += 1
        oi_text = "\n\n".join(oi_parts)
        if oi_text and len(oi_text) < budget // 4:
            sections.append(f"[Open Items]\n{oi_text}")
            budget -= len(oi_text) + 20

    for domain in domains:
        if domain == "general":
            continue
        per_domain = budget // max(len(domains), 1)

        # 1. Prompt file (domain knowledge, always readable)
        prompt_file = _PROMPTS_DIR / f"{domain}.md"
        if prompt_file.exists():
            try:
                ptxt = prompt_file.read_text(encoding="utf-8", errors="replace")
                ptxt = _strip_frontmatter(ptxt)
                if len(ptxt) > per_domain // 2:
                    ptxt = ptxt[:per_domain // 2] + "\n…[truncated]"
                sections.append(f"[Domain Prompt: {domain}]\n{ptxt}")
                budget -= len(ptxt) + 30
            except OSError:
                pass

        # 2. State file (if readable / not encrypted)
        state_key = _DOMAIN_TO_STATE_FILE.get(domain)
        if state_key:
            content, _ = _read_state_file(state_key)
            if content:
                content = _strip_frontmatter(content)
                remaining = min(per_domain // 2, budget)
                if len(content) > remaining:
                    content = content[:remaining] + "\n…[truncated]"
                sections.append(f"[State: {domain}]\n{content}")
                budget -= len(content) + 20

        # 3. Also try reading unencrypted state files not in the whitelist
        direct_state = _STATE_DIR / f"{domain}.md"
        if direct_state.exists() and state_key is None:
            try:
                stxt = direct_state.read_text(encoding="utf-8", errors="replace")
                stxt = _strip_frontmatter(stxt)
                remaining = min(per_domain // 2, budget)
                if len(stxt) > remaining:
                    stxt = stxt[:remaining] + "\n…[truncated]"
                sections.append(f"[State: {domain}]\n{stxt}")
                budget -= len(stxt) + 20
            except OSError:
                pass

    return "\n\n".join(sections)


# ── _detect_llm_cli ─────────────────────────────────────────────

def _detect_llm_cli() -> tuple[str, list[str]] | None:
    """Detect available LLM CLI. Returns (executable, base_args) or None.

    Preference order: copilot (sonnet-4-6), gemini (3.1-flash), claude (sonnet-4-6).
    """
    # Copilot CLI — primary
    copilot = shutil.which("copilot")
    if copilot:
        return copilot, ["--yolo", "--model", "claude-sonnet-4-6"]
    # Gemini CLI — first fallback
    gemini = shutil.which("gemini")
    if gemini:
        return gemini, ["--yolo", "--model", "gemini-3.1-flash"]
    # Claude — last resort
    claude = shutil.which("claude")
    if claude:
        return claude, ["--dangerously-skip-permissions", "--model", "claude-sonnet-4-6"]
    return None


# ── _detect_all_llm_clis ────────────────────────────────────────

def _detect_all_llm_clis() -> list[tuple[str, str, list[str]]]:
    """Return all available CLIs as (name, executable, base_args).

    Preference order: copilot (default model), gemini (2.5-pro), claude (sonnet-4-6).
    """
    clis: list[tuple[str, str, list[str]]] = []
    copilot = shutil.which("copilot")
    if copilot:
        # Do NOT pass --model: GitHub Copilot CLI uses its own model identifiers
        # and "claude-sonnet-4-6" is not a valid name in that namespace.
        clis.append(("copilot", copilot, ["--yolo"]))
    gemini = shutil.which("gemini")
    if gemini:
        clis.append(("gemini", gemini, ["--yolo", "--model", "gemini-2.5-pro"]))
    claude = shutil.which("claude")
    if claude:
        clis.append(("claude", claude, ["--dangerously-skip-permissions", "--model", "claude-sonnet-4-6"]))
    return clis


# ── _vault_relock_if_needed ─────────────────────────────────────

def _vault_relock_if_needed() -> None:
    """Re-encrypt vault if any .age files have decrypted .md siblings on disk."""
    import subprocess as _sp
    age_files = list(_STATE_DIR.glob("*.md.age"))
    for af in age_files:
        plain = _STATE_DIR / af.name.replace(".md.age", ".md")
        if plain.exists():
            log.warning("[vault] decrypted file found: %s — re-encrypting", plain.name)
            try:
                _sp.run(
                    [_sys.executable, str(_ARTHA_DIR / "scripts" / "vault.py"), "encrypt"],
                    cwd=str(_ARTHA_DIR),
                    timeout=30,
                    capture_output=True,
                )
                log.info("[vault] re-encrypted successfully")
            except Exception as exc:
                log.error("[vault] re-encrypt failed: %s", exc)
            return  # one encrypt call handles all files


# ── _call_single_llm ────────────────────────────────────────────

async def _call_single_llm(
    name: str,
    executable: str,
    base_args: list[str],
    prompt_text: str,
    instruction: str,
    timeout: int = _LLM_TIMEOUT_SEC,
) -> str:
    """Call a single LLM CLI and return its response."""
    import tempfile
    import re as _re

    with tempfile.TemporaryDirectory() as tmpdir:
        prompt_file = Path(tmpdir) / "prompt.txt"
        prompt_file.write_text(prompt_text, encoding="utf-8")

        # Run CLIs from the Artha workspace so they pick up project config
        # (e.g. Claude loads CLAUDE.md, skills, and can invoke vault/tools).
        workspace = str(_ARTHA_DIR)

        # Each CLI has a different stdin/file-reading pattern
        if name == "gemini":
            # Gemini reads stdin
            args_str = " ".join(base_args)
            shell_cmd = f'type "{prompt_file}" | "{executable}" -p "{instruction}" {args_str}'
        elif name == "copilot":
            # Copilot reads files via --add-dir + tool use; pass full path
            args_str = " ".join(base_args)
            shell_cmd = f'"{executable}" -p "Read the file {prompt_file} and {instruction}" {args_str} --add-dir "{tmpdir}"'
        else:
            # Claude reads stdin — runs from workspace to access skills/CLAUDE.md
            args_str = " ".join(base_args)
            shell_cmd = f'type "{prompt_file}" | "{executable}" -p "{instruction}" {args_str}'

        try:
            _t0 = time.monotonic()
            proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            _latency_ms = (time.monotonic() - _t0) * 1000
            raw = stdout.decode("utf-8", errors="replace")
            raw = _re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', raw)
            lines = raw.splitlines()
            clean_lines = [
                l for l in lines
                if not l.startswith("Loaded cached credentials")
                and not l.startswith("YOLO mode")
                and l.strip()
            ]
            result = "\n".join(clean_lines).strip()
            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                log.warning("[%s] exited with code %d (stderr: %s) — treating as failure", name, proc.returncode, err[:200])
                # RD-49: trace failed LLM call
                try:
                    from lib.observability import llm_trace as _llm_trace  # noqa: PLC0415
                    _llm_trace(caller="llm_bridge._call_single_llm", model=name, latency_ms=_latency_ms, error=f"exit:{proc.returncode}")
                except Exception:  # noqa: BLE001
                    pass
                # RD-51: raise typed error so callers can detect LLM unavailability
                raise LLMUnavailableError(
                    reason=err[:200] if err else "non_zero_exit",
                    last_exit_code=proc.returncode,
                )
            if not result:
                err = stderr.decode("utf-8", errors="replace").strip()
                log.warning("[%s] stdout empty, stderr: %s", name, err[:200])
                # RD-49: trace empty response
                try:
                    from lib.observability import llm_trace as _llm_trace  # noqa: PLC0415
                    _llm_trace(caller="llm_bridge._call_single_llm", model=name, latency_ms=_latency_ms, error="empty_response")
                except Exception:  # noqa: BLE001
                    pass
                raise LLMUnavailableError(
                    reason=f"empty_response:{err[:100]}" if err else "empty_response",
                    last_exit_code=proc.returncode or 0,
                )
            # RD-49: trace successful LLM call
            try:
                from lib.observability import llm_trace as _llm_trace  # noqa: PLC0415
                _completion_tokens = len(result.split())  # word-count approx; real token count not available from CLI
                _llm_trace(
                    caller="llm_bridge._call_single_llm",
                    model=name,
                    completion_tokens=_completion_tokens,
                    latency_ms=_latency_ms,
                )
            except Exception:  # noqa: BLE001
                pass
            return result
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            log.warning("[%s] timed out after %ds", name, timeout)
            # RD-49: trace timeout
            try:
                from lib.observability import llm_trace as _llm_trace  # noqa: PLC0415
                _llm_trace(caller="llm_bridge._call_single_llm", model=name, latency_ms=float(timeout * 1000), error="timeout")
            except Exception:  # noqa: BLE001
                pass
            # RD-51: raise typed error instead of returning ""
            raise LLMUnavailableError(reason="subprocess_timeout", last_exit_code=-1)
        except LLMUnavailableError:
            raise  # propagate typed errors unchanged
        except FileNotFoundError as exc:
            log.error("[%s] binary not found: %s", name, exc)
            raise LLMUnavailableError(reason="binary_not_found", last_exit_code=-1) from exc
        except Exception as exc:
            log.error("[%s] subprocess failed: %s", name, exc)
            raise LLMUnavailableError(reason=f"subprocess_error:{type(exc).__name__}", last_exit_code=-1) from exc
        finally:
            # Safety net: if the CLI decrypted the vault (Claude skills),
            # re-encrypt immediately so plaintext never lingers on disk.
            _vault_relock_if_needed()


# ── _ask_llm ────────────────────────────────────────────────────

async def _ask_llm(question: str, context: str) -> str:
    """Send a context-aware question to the LLM CLI with failover."""

    clis = _detect_all_llm_clis()
    if not clis:
        return "No LLM CLI available (install gemini, copilot, or claude)."

    system_prompt = (
        "You are Artha, a personal intelligence assistant. "
        "Answer the user's question using ONLY the context provided below. "
        "Be concise and actionable. If the context doesn't contain enough "
        "information to answer, say so clearly.\n"
        "FORMAT: Use numbered lists (1. 2. 3.) for ranked/sequential items, "
        "bullet points (• ) for unordered items, and blank lines between sections. "
        "Lead with a one-line direct answer, then supporting detail. "
        "No markdown (no **, ##, ```) — plain text with Unicode bullets only."
    )

    full_prompt = f"{system_prompt}\n\n--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n\nQuestion: {question}"

    # Try each CLI in order until one succeeds
    for name, executable, base_args in clis:
        try:
            result = await _call_single_llm(
                name, executable, base_args, full_prompt,
                "Answer the question above.",
            )
        except LLMUnavailableError as exc:
            log.warning("[ask] %s unavailable (%s), trying next...", name, exc.reason)
            continue
        if result:
            log.info("[ask] answered by %s (%d chars)", name, len(result))
            return result
        log.warning("[ask] %s failed or empty, trying next...", name)

    return _get_error_message("connector_timeout") or "All LLM CLIs failed. Try again later."


# ── _ask_llm_ensemble ───────────────────────────────────────────

async def _ask_llm_ensemble(question: str, context: str) -> str:
    """Ask all available LLMs in parallel, then consolidate into one answer.

    Read-only operation — safe to run concurrently.
    """
    clis = _detect_all_llm_clis()
    if not clis:
        return "No LLM CLI available (install gemini, copilot, or claude)."
    if len(clis) < 2:
        # Only one CLI — just use normal path
        return await _ask_llm(question, context)

    system_prompt = (
        "You are Artha, a personal intelligence assistant. "
        "Answer the user's question using ONLY the context provided below. "
        "Be concise and actionable. If the context doesn't contain enough "
        "information to answer, say so clearly.\n"
        "FORMAT: Use numbered lists (1. 2. 3.) for ranked/sequential items, "
        "bullet points (• ) for unordered items, and blank lines between sections. "
        "Lead with a one-line direct answer, then supporting detail. "
        "No markdown (no **, ##, ```) — plain text with Unicode bullets only."
    )

    full_prompt = f"{system_prompt}\n\n--- CONTEXT ---\n{context}\n--- END CONTEXT ---\n\nQuestion: {question}"

    # Ask all CLIs in parallel
    tasks = [
        _call_single_llm(name, exe, args, full_prompt, "Answer the question above.")
        for name, exe, args in clis
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect successful responses with source labels
    responses: list[tuple[str, str]] = []
    for (name, _, _), result in zip(clis, raw_results):
        if isinstance(result, LLMUnavailableError):
            log.warning("[ask-all] %s unavailable (%s)", name, result.reason)
        elif isinstance(result, Exception):
            log.warning("[ask-all] %s error: %s", name, result)
        elif result:
            responses.append((name, result))

    if not responses:
        return _get_error_message("connector_timeout") or "All LLM CLIs failed. Try again later."
    if len(responses) == 1:
        name, answer = responses[0]
        log.info("[ask-all] only %s responded (%d chars)", name, len(answer))
        return answer

    # Consolidate via Haiku — fast & cheap for synthesis
    import shutil as _shutil
    claude_exe = _shutil.which("claude")
    labeled = "\n\n".join(
        f"--- Response from {name} ---\n{resp}" for name, resp in responses
    )
    consolidation_prompt = (
        "You are given multiple AI responses to the same question. "
        "Synthesize ONE best answer that is accurate, concise, and complete. "
        "Prefer concrete facts over hedging. Resolve any contradictions by "
        "favouring the response with more specific detail. "
        "Do NOT mention which AI said what — just give the single best answer.\n"
        "FORMAT: Use numbered lists (1. 2. 3.) for ranked/sequential items, "
        "bullet points (• ) for unordered items, and blank lines between sections. "
        "Lead with a one-line direct answer, then supporting detail. "
        "No markdown (no **, ##, ```) — plain text with Unicode bullets only.\n\n"
        f"Original question: {question}\n\n{labeled}"
    )

    log.info(
        "[ask-all] got %d responses (%s), consolidating via haiku",
        len(responses),
        "+".join(n for n, _ in responses),
    )

    if claude_exe:
        try:
            final = await _call_single_llm(
                "claude-haiku", claude_exe,
                ["--dangerously-skip-permissions", "--model", "haiku"],
                consolidation_prompt, "Synthesize the best answer now.",
            )
        except LLMUnavailableError:
            final = ""
    else:
        # Fallback: use primary CLI if Claude not available
        consolidator = clis[0]
        try:
            final = await _call_single_llm(
                consolidator[0], consolidator[1], consolidator[2],
                consolidation_prompt, "Synthesize the best answer now.",
            )
        except LLMUnavailableError:
            final = ""
    if not final:
        # Consolidation failed — return longest individual response
        final = max((r for _, r in responses), key=len)
    return final


# ── cmd_ask ─────────────────────────────────────────────────────

async def cmd_ask(question: str, scope: str, *, is_bridge: bool = True) -> tuple[str, str]:
    """Context-aware Q&A — routes free-form questions to LLM with Artha context.

    Prefix with 'aa' (or 'ask all') to run ensemble mode (all CLIs in parallel).
    Set is_bridge=False when called from the primary CLI to allow encrypted-domain access.
    """
    if not question.strip():
        return "Send me a question and I'll answer using your Artha data.", "N/A"

    # Check for ensemble trigger
    ensemble = False
    q = question.strip()
    for prefix in ("aa ", "ask all ", "ask-all "):
        if q.lower().startswith(prefix):
            q = q[len(prefix):].strip()
            ensemble = True
            break

    # Intent classification — runs before keyword domain detection.
    intent = _classify_intent(q)

    # Bridge path: block access to encrypted domains for security.
    if intent and is_bridge:
        blocked_domains = _INTENT_ENCRYPTED_DOMAINS.get(intent, [])
        if blocked_domains:
            domain_list = ", ".join(blocked_domains)
            return (
                f"That domain ({domain_list}) contains sensitive data. "
                "For security, I can only access it from your terminal session. "
                "Open your CLI and ask again there.",
                "N/A",
            )

    if intent:
        context = _gather_intent_context(intent)
        log.info("[ask] intent=%r question=%r context_chars=%d ensemble=%s",
                 intent, q[:80], len(context), ensemble)
        _audit_log("CHANNEL_ASK", question=q[:100], intent=intent,
                   context_chars=len(context), ensemble=ensemble)
    else:
        # Fallback: keyword domain detection (existing path).
        domains = _detect_domains(q)
        context = _gather_context(domains)
        log.info("[ask] question=%r domains=%s context_chars=%d ensemble=%s",
                 q[:80], domains, len(context), ensemble)
        _audit_log("CHANNEL_ASK", question=q[:100], domains=",".join(domains),
                   context_chars=len(context), ensemble=ensemble)

    # Call LLM(s)
    try:
        if ensemble:
            answer = await _ask_llm_ensemble(q, context)
        else:
            answer = await _ask_llm(q, context)
    except Exception as _exc:
        log.error("[ask] unexpected error: %s", _exc)
        return _get_error_message("python_traceback") or "An unexpected error occurred.", "N/A"

    # Truncate to Telegram limit
    if len(answer) > 3800:
        answer = answer[:3800] + "\n…[truncated]"

    return answer, "N/A"
