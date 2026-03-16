# Prompt Overlay System

Artha uses a **base + overlay** architecture for domain prompts:

- **Base prompts** (`prompts/<domain>.md`) — Universal extraction logic,
  alert thresholds, state update protocols, and standard sender signatures.
  These ship with Artha and are version-controlled.

- **Routing overlay** (`config/routing.yaml`) — User-specific sender/subject
  pattern-to-domain mapping. Generated from `user_profile.yaml` by
  `generate_identity.py`. Customizes _which emails go where_ without
  modifying prompt files.

- **Prompt overlays** (`config/prompt-overlays/<domain>.md`) — Optional
  per-user additions to domain prompts. Use these to add extraction rules,
  alert thresholds, or context that's specific to your situation without
  modifying the base prompts.

## How Overlays Work

During catch-up, when the AI loads a domain prompt, it should:

1. Load `prompts/<domain>.md` (base — always present)
2. Check `config/prompt-overlays/<domain>.md` (overlay — optional)
3. If overlay exists, append its content after the base prompt

This means:
- Base prompt rules always apply
- Overlay rules add to (never replace) the base
- Users can safely update Artha without losing their customizations

## Creating an Overlay

```bash
# Example: add custom finance rules
cat > config/prompt-overlays/finance.md << 'EOF'
## Custom Finance Rules

### Additional Sender Signatures
- `*@localcreditunion.com` → route to finance
- `*@propertymanager.com` → route to finance (rental income)

### Custom Alert Thresholds
🟠 URGENT: Rental income not received by 5th of month

### Additional Extraction Rules
- For rental income emails: extract tenant name, amount, late fee
EOF
```

## Architecture Diagram

```
prompts/finance.md              ← Base (shipped, version-controlled)
     +
config/prompt-overlays/finance.md   ← Overlay (user-specific, gitignored)
     +
config/routing.yaml             ← Routing (auto-generated from profile)
     =
Complete domain prompt context for AI
```

## Files in this directory

Place `<domain>.md` files here to extend the corresponding base prompt.
Domain names must match exactly (e.g., `immigration.md`, `finance.md`,
`kids.md`, `health.md`).

This directory is gitignored — your customizations won't be overwritten
by updates.
