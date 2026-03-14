# Phase 4 — Reason (Steps 8–11)

## Steps

### Step 8: Cross-domain reasoning
- Connect signals across domains that amplify or conflict:
  - Immigration deadline + finance (backup plan if case denied)
  - Kid's college timeline + finance/savings
  - Health appointment + calendar conflicts
  - Travel plans + immigration (advance parole requirements)
- Generate "cross-domain insights" section for briefing
- This is the highest-value step — it produces intelligence no single-domain
  analysis can provide

### Step 9: Web research (if needed)
- Triggered only by time-sensitive external data:
  - Visa Bulletin changes (monthly)
  - Regulatory updates affecting immigration path
  - Weather advisories for travel plans
- Uses skill_runner.py data, not live web browsing

### Step 10: Ensemble reasoning (high-stakes only)
- For P0 decisions (immigration deadlines, large financial moves):
  - Present multiple risk scenarios
  - Include confidence levels
  - Flag "this needs a professional" when stakes exceed self-serve threshold

### Step 11: Synthesize briefing
- Assemble the final briefing from all processed domains
- Format per `config/briefing-formats.md` (standard/flash/deep)
- Priority ordering: P0 alerts → P1 actions → P2 info → P3 awareness
- Archive to `briefings/YYYY-MM-DD.md`

## Error handling
- Cross-domain reasoning failures produce partial briefing (better than nothing)
- Research failures are logged but don't block briefing
