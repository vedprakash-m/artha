# Learned Procedures

This directory stores procedures Artha learned from experience.

Each file describes a working approach for a non-trivial task discovered
during catch-up sessions. Procedures are created automatically during
Step 11c when a task:
- Required 5+ tool calls or file operations to complete
- Involved error recovery or dead ends
- Resulted in the user correcting the initial approach

**File naming:** `{domain}-{slug}.md`
**Example:** `immigration-uscis-status-ioe-format.md`

**Do NOT manually edit** — managed by Artha's procedure extraction pipeline.
Set `harness.agentic.procedural_memory.enabled: false` to disable.
