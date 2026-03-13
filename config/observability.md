## §13 Observability & Retrospective *(v2.1)*

### Post-session calibration (Step 19 details)
The calibration system learns over time:
- Questions get more targeted as Artha builds domain accuracy history
- Skip rate tracked per user: if consistently skipped, frequency auto-reduces
- Correction patterns logged to memory.md feed back into domain extraction logic

### Monthly retrospective
Generated on the 1st of each month if last retro was >28 days ago (Step 3 trigger).
Saved to `summaries/YYYY-MM-retro.md`. The format is §8.10.
A brief 3-line summary is embedded in the monthly 1st catch-up briefing footer:
`📊 Monthly retro saved: [N] catch-ups · [acceptance_rate]% action rate · [signal:noise]% signal`

### `/diff` implementation notes
The `/diff` command uses git log on the `state/` directory. To enable:
```bash
git init        # if not already a git repo
git add state/
git commit -m "Artha state baseline [date]"
```
Artha automatically commits state/ after each `vault.py encrypt` cycle (Step 18) if git is configured:
```bash
git add state/*.md state/*.md.age && \
git commit -m "Artha catch-up [ISO datetime]" --quiet || true
```
Non-blocking: if git fails, continue without committing. Log git commit status to health-check.md.

---

