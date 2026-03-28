"""channel/llm_bridge.py — LLM CLI abstraction, failover, ensemble."""
from __future__ import annotations
import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from channel.state_readers import _read_state_file, _apply_scope_filter, _DOMAIN_TO_STATE_FILE
from channel.formatters import _trim_to_cap, _extract_section_summaries, _strip_frontmatter
from channel.audit import _audit_log

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

    Preference order: claude (sonnet), gemini (flash), copilot (sonnet).
    """
    # Claude Code — fastest, cleanest output
    claude = shutil.which("claude")
    if claude:
        return claude, ["--dangerously-skip-permissions", "--model", "sonnet"]
    # Gemini CLI — free, good quality
    gemini = shutil.which("gemini")
    if gemini:
        return gemini, ["--yolo"]
    # Copilot CLI — slowest, noisy output
    copilot = shutil.which("copilot")
    if copilot:
        return copilot, ["--yolo", "-s", "--model", "claude-sonnet-4"]
    return None


# ── _detect_all_llm_clis ────────────────────────────────────────

def _detect_all_llm_clis() -> list[tuple[str, str, list[str]]]:
    """Return all available CLIs as (name, executable, base_args)."""
    clis: list[tuple[str, str, list[str]]] = []
    claude = shutil.which("claude")
    if claude:
        clis.append(("claude", claude, ["--dangerously-skip-permissions", "--model", "sonnet"]))
    gemini = shutil.which("gemini")
    if gemini:
        clis.append(("gemini", gemini, ["--yolo"]))
    copilot = shutil.which("copilot")
    if copilot:
        clis.append(("copilot", copilot, ["--yolo", "-s", "--model", "claude-sonnet-4"]))
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
                    [sys.executable, str(_ARTHA_DIR / "scripts" / "vault.py"), "encrypt"],
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
            # Copilot reads files via --add-dir + tool use
            args_str = " ".join(base_args)
            shell_cmd = f'"{executable}" -p "Read prompt.txt and {instruction}" {args_str} --add-dir "{tmpdir}"'
        else:
            # Claude reads stdin — runs from workspace to access skills/CLAUDE.md
            args_str = " ".join(base_args)
            shell_cmd = f'type "{prompt_file}" | "{executable}" -p "{instruction}" {args_str}'

        try:
            proc = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
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
            if not result:
                err = stderr.decode("utf-8", errors="replace").strip()
                log.warning("[%s] stdout empty, stderr: %s", name, err[:200])
                return ""
            return result
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            log.warning("[%s] timed out after %ds", name, timeout)
            return ""
        except Exception as exc:
            log.error("[%s] subprocess failed: %s", name, exc)
            return ""
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
        result = await _call_single_llm(
            name, executable, base_args, full_prompt,
            "Answer the question above.",
        )
        if result:
            log.info("[ask] answered by %s (%d chars)", name, len(result))
            return result
        log.warning("[ask] %s failed or empty, trying next...", name)

    return "All LLM CLIs failed. Try again later."


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
    results = await asyncio.gather(*tasks)

    # Collect successful responses with source labels
    responses: list[tuple[str, str]] = []
    for (name, _, _), result in zip(clis, results):
        if result:
            responses.append((name, result))

    if not responses:
        return "All LLM CLIs failed. Try again later."
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
        final = await _call_single_llm(
            "claude-haiku", claude_exe,
            ["--dangerously-skip-permissions", "--model", "haiku"],
            consolidation_prompt, "Synthesize the best answer now.",
        )
    else:
        # Fallback: use primary CLI if Claude not available
        consolidator = clis[0]
        final = await _call_single_llm(
            consolidator[0], consolidator[1], consolidator[2],
            consolidation_prompt, "Synthesize the best answer now.",
        )
    if not final:
        # Consolidation failed — return longest individual response
        final = max((r for _, r in responses), key=len)
    return final


# ── cmd_ask ─────────────────────────────────────────────────────

async def cmd_ask(question: str, scope: str) -> tuple[str, str]:
    """Context-aware Q&A — routes free-form questions to LLM with Artha context.

    Prefix with 'aa' (or 'ask all') to run ensemble mode (all CLIs in parallel).
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

    # Detect relevant domains and gather context
    domains = _detect_domains(q)
    context = _gather_context(domains)

    log.info("[ask] question=%r domains=%s context_chars=%d ensemble=%s",
             q[:80], domains, len(context), ensemble)

    _audit_log("CHANNEL_ASK", question=q[:100], domains=",".join(domains),
               context_chars=len(context), ensemble=ensemble)

    # Call LLM(s)
    if ensemble:
        answer = await _ask_llm_ensemble(q, context)
    else:
        answer = await _ask_llm(q, context)

    # Truncate to Telegram limit
    if len(answer) > 3800:
        answer = answer[:3800] + "\n…[truncated]"

    return answer, "N/A"
