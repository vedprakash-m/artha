# IoT / Home Assistant Integration — Artha Enhancement Specification

**Codename:** ARTHA-IOT  
**Version:** 1.4.0  
**Date:** 2026-03-20  
**Author:** Lead Principal System Architect  
**Status:** APPROVED FOR IMPLEMENTATION (Waves 1–2). CQ-HA1 PARTIALLY RESOLVED, CQ-HA6 RESOLVED. CQ-HA2–HA5 non-blocking (defaults safe for Wave 1–2). Gap resolutions: auth contract fixed (G1), ha_url in connectors.yaml (G2). See §3.1b.  
**Review:** v1.0.0 → v1.1.0 internal arch review (6C/5H/4M); v1.1.0 → v1.2.0 external review (7 findings); v1.2.0 → v1.3.0 third-pass review (4C/4H/4M); v1.3.0 → v1.4.0 gap resolution + implementation approval  
**Depends on:** ACT v1.3.0, PRD v7.0.6 (F7.4, F12.5), Tech Spec v3.9.7  
**Baseline:** 1,243 tests passing, 13 connectors (8 enabled), 16 skills, 8 action handlers, 18 signal types  
**PRD References:** F7.4 (P1 — Home Assistant Integration), F12.5 (P2 — HA Health Monitor), OQ-8 (local URL + long-lived token)

---

## §0 — Executive Summary

### Thesis

Home Assistant (HA) running at `192.168.1.123:8123` on a Raspberry Pi consolidates
28 network devices across 6 ecosystems (Apple, Amazon, Google, Sonos, Ring, Gecko)
behind a single REST API with a single long-lived access token. This spec integrates
HA into Artha's existing **read–reason–act** pipeline using zero new architectural
paradigms:

- **1 connector** (`scripts/connectors/homeassistant.py`) — implements the existing `ConnectorHandler` Protocol
- **1 skill** (`scripts/skills/home_device_monitor.py`) — subclasses the existing `BaseSkill` ABC
- **Phase 2: 1 action handler** (`scripts/actions/homeassistant_service.py`) — registered in the existing `_HANDLER_MAP`

HA is the **universal adapter layer**. One connector, one token, one REST endpoint
covers all 28 devices. Without HA, Artha would need separate integrations for Ring
(OAuth2 + proprietary API), Amazon Alexa (Skills API + LWA), Google Home (Device
Access API + OAuth2), Sonos (Cloud API + OAuth2), and Gecko (proprietary protocol).
HA has already solved the hard problem of device-level protocol negotiation.

### Why This Enhancement

| Filter | Assessment |
|--------|------------|
| **Architecture Fit** | HIGH — Maps cleanly to ConnectorHandler Protocol + BaseSkill ABC + DomainSignal → ActionProposal pipeline. No new patterns. |
| **Privacy Compliance** | HIGH — All traffic stays on local LAN (no cloud relay). Token stored in system keyring. PII guard scans all entity names. No IP cameras or microphones ingested. |
| **Value Density** | HIGH — PRD F7.4 is P1 priority (never implemented). 28 devices currently invisible to Artha. Cross-domain intelligence (travel + security, energy + finance) unlocks compound signals. |

### Design Principles (all inherited from ACT v1.3.0)

1. **Propose, never presume** — Phase 1 is strictly **read-only**. Phase 2 device control requires explicit user approval and separate specification.
2. **Deterministic before heuristic** — Single-domain alert thresholds are numeric and code-evaluated (energy spike >30%, device offline >2h). No LLM inference on device states in Waves 1-2. Wave 3 cross-domain compound signals are inherently heuristic (LLM-correlated in Step 8f) — this is by design, as multi-domain correlation requires contextual reasoning that deterministic thresholds cannot capture.
3. **Fail-open for reads, fail-closed for writes** — HA connector failure is non-blocking (like WorkIQ). Catch-up continues with available data.
4. **The queue is the product** — Device anomalies produce `DomainSignal` objects that flow through `ActionComposer` → `ActionQueue`.
5. **Privacy is load-bearing** — Entity names pass through PII guard. IP addresses never written to state files. No audio/video/camera data ingested.
6. **Incremental delivery** — Strangler Fig: Phase 1 (read-only visibility), Phase 2 (trusted device control). Each phase ships independently.

### Architectural Decision: Self-Contained Connector+Skill vs. Framework Seam

**Context:** The HA connector writes `tmp/ha_entities.json` as a side effect for its
companion skill (§3.1a). This creates a deliberate coupling between one connector and
one skill — a pattern that does not exist elsewhere in Artha. The standard pipeline
streams JSONL to stdout; skills fetch their own data independently via `pull()`.

**Options evaluated:**

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Skill-owned fetch** | Skill calls HA API directly in `pull()`, bypassing the connector entirely. | Fastest delivery. No temp file. But duplicates fetch/auth logic and HA data doesn't appear in pipeline JSONL (invisible to observability). |
| **B: Self-contained coupling** (chosen) | Connector writes temp artifact; companion skill reads it. Coupling is explicit and documented. | One-off exception to the connector contract. Clean if HA remains the only connector-backed skill. Adds disk write (privacy surface — see §3.1a privacy contract). |
| **C: Generic artifact registry** | Introduce a `ConnectorArtifact` protocol that any connector can use to persist structured output for skill consumption. | Best long-term. But premature — no other connector needs this today. Over-engineering a framework seam for a single consumer. |

**Decision:** Option B — self-contained coupling for Wave 1.

**Escalation trigger:** If a second connector-backed skill is ever proposed (e.g., a
dedicated HVAC skill reading from a Nest connector), revisit Option C and introduce
a generic artifact registry at that point. Until then, the one-off coupling is acceptable
and explicitly documented. This follows YAGNI — don't build framework for hypothetical
consumers.

### Scope Boundaries

| IN SCOPE | OUT OF SCOPE |
|----------|-------------|
| Device state reading via HA REST API | Direct device protocol communication (Zigbee, Z-Wave, Thread) |
| Entity status, attributes, last_changed | Camera streams, audio from Echo/Sonos, video doorbells |
| Energy consumption from HA energy dashboard | Smart meter direct integration (AMI/Green Button) |
| Automation status monitoring | Writing/modifying HA automations |
| Presence detection (if HA-configured, consent-gated) | GPS tracking or phone location services |
| Phase 2: service calls for device control | Phase 2: HVAC scheduling from Artha (HA owns schedules) |

---

## §1 — AS-IS Architecture & Gap Analysis (Phase A: Discovery)

### 1.1 Current Home Domain State

The home domain (`prompts/home.md`, `state/home.md`) tracks:
- Property details (address, purchase info)
- Mortgage (monthly payment, lender)
- HOA (quarterly dues, association name)
- Utilities (~$600+/mo: electricity, water, waste, internet)
- Insurance (homeowner policy, open risks)
- Maintenance items (open/monitoring)

**What's missing:** Zero visibility into 28 connected devices, energy patterns, security
system status, automation health, or device lifecycle. The swim spa has a "Gecko in.touch 2"
Wi-Fi controller that's invisible to Artha. Ring Floodlight (front security camera) status
is unknown without checking the Ring app manually.

### 1.2 Network Inventory (from user-provided scan)

| Device | IP | Ecosystem | HA Relevance |
|--------|-----|-----------|-------------|
| ASUS GT-BE98 Pro (router) | .1 | Network | Infrastructure health |
| Home Assistant (RPi) | .123:8123 | HA Core | API endpoint |
| Apple TV 4K (×2) | .66, .73 | Apple/Matter | Matter controllers, Thread border routers |
| Sonos (×2) | .94, .164 | Sonos | Media, could be announcements (Phase 2) |
| Ring Floodlight (front) | .23 | Amazon Ring | Security — **was offline (OI-043)** |
| Amazon Echo (×2) | .54, .152 | Amazon Alexa | Voice control (out of scope for Artha) |
| Amazon Fire TV (×2) | .46, .151 | Amazon Fire | Entertainment (low priority) |
| Amazon Ring Indoor | .154 | Amazon Ring | Security |
| Brother HL-L2350DW | .169 | Printer | Toner 40%, drum 89%, 1377 pages |
| Google Nest Hub | .161 | Google Home | Display/control hub |
| Gecko in.touch 2 (swim spa) | TBD | Gecko/HA | Water temp, filtration, energy |
| Apple devices (×3) | .34, .35, .233 | Apple | Presence detection (consent-gated) |
| Computers (×4) | .235, .83, .17, .132 | Various | Low priority |
| TVs (×3) | .74, .51, .165 | Various | Power state only |
| Private MAC devices (×3) | Various | Unknown | Excluded |

### 1.3 PRD Requirements (Planned but Never Implemented)

**F7.4 (P1):** "Read device status, energy usage, and automation logs from Home Assistant
local API. Surface anomalies (device offline, unusual energy consumption) in daily briefing."

**F12.5 (P2):** "Track Home Assistant system uptime, device offline alerts, and automation
failures. Surface in morning briefing if any critical device is offline."

**OQ-8 (Resolved):** "Home Assistant: local URL + long-lived token."

**Tech Spec §3.5 Connector Table:** `Home Assistant | Local API | LAN Token | requests | Smart home status (Phase 2)`

### 1.4 Pain Points

| # | Pain Point | Evidence | Current Workaround |
|---|-----------|----------|-------------------|
| 1 | Ring Floodlight offline → zero alerting | OI-043 discovered manually | Check Ring app periodically |
| 2 | No energy cost correlation | PSE bill ~$300/mo with no device-level attribution | Manual PSE account login |
| 3 | Swim spa status invisible | Gecko in.touch 2 has Wi-Fi; no monitoring | Check spa panel physically |
| 4 | Travel mode has no security awareness | No knowledge of which cameras/sensors are active | Manually arm Ring system before travel |
| 5 | Printer consumables unknown to Artha | Brother reports toner 40% — not surfaced | Check printer display panel |
| 6 | No presence awareness for family coordination | Artha doesn't know who's home | Manual communication |

### 1.5 HA REST API Surface (Verified from HA 2026.3.2 Documentation)

| Endpoint | Method | Purpose | Phase |
|----------|--------|---------|-------|
| `/api/` | GET | API health check (returns `{"message": "API running."}`) | 1 |
| `/api/config` | GET | HA configuration (version, location, unit system) | 1 |
| `/api/states` | GET | All entity states (full dump) | 1 |
| `/api/states/{entity_id}` | GET | Single entity state + attributes | 1 |
| `/api/history/period/{timestamp}` | GET | Historical state changes (24h default) | 1 |
| `/api/services` | GET | List available service domains | 2 |
| `/api/services/{domain}/{service}` | POST | Call a service (turn on/off, set temp, etc.) | 2 |
| `/api/events` | GET | List event types | 2 |

**Authentication:** `Authorization: Bearer <LONG_LIVED_ACCESS_TOKEN>` header on every request.

**Rate limiting:** HA local API has no rate limiting. However, Artha should self-impose
limits to avoid overwhelming the Raspberry Pi (ARM SoC, limited RAM).

---

## §2 — Architectural Design (Phase B: Design Proposal)

### 2.1 Option Analysis

#### Option A: Connector-Only (Minimal)

Add HA as a pipeline connector. Entity states fetched during Step 4, processed inline
by the AI during Step 7 (home domain processing). No dedicated skill.

**Pros:** Minimal code (~150 LOC). Follows existing connector pattern exactly.  
**Cons:** No deterministic alerting. AI must reason about device states each session.
No delta detection between catches. No cross-domain signal routing.

#### Option B: Connector + Skill (Recommended)

Add HA as both a connector (data fetch) AND a skill (deterministic analysis).
The connector feeds raw entity states into `tmp/ha_entities.json`.
The skill reads cached data, applies deterministic thresholds, and emits `DomainSignal`
objects that flow through the existing action pipeline.

**Pros:** Deterministic alerting (device offline >2h = signal). Delta detection via
`BaseSkill.compare_fields`. Cross-domain signal routing via `ActionComposer`.
Matches the pattern of every other high-value data source in Artha.  
**Cons:** More code (~550 LOC production). Two registration points (connectors.yaml + skills.yaml).

#### Option C: Connector + Skill + Dedicated Domain

Create a new `iot` or `smart_home` domain with its own prompt file, state file, and
routing rules. Full domain-level treatment.

**Pros:** Clean domain separation. Own briefing section.  
**Cons:** Over-engineered. IoT data enriches the existing `home` domain — it doesn't
warrant separate domain treatment. Would fragment home intelligence across two state
files. Contradicts the "home is one domain" principle.

### 2.2 Trade-Off Matrix

| Criterion | Option A (Connector) | Option B (Connector+Skill) | Option C (Full Domain) |
|-----------|---------------------|---------------------------|----------------------|
| **Code Volume** | ~150 LOC | ~550 LOC | ~900 LOC |
| **Architecture Alignment** | Good | **Best** | Over-engineered |
| **Deterministic Alerting** | ❌ AI-dependent | ✅ Threshold-based | ✅ Threshold-based |
| **Delta Detection** | ❌ None | ✅ BaseSkill.compare_fields | ✅ Same |
| **Cross-Domain Signals** | ❌ Manual | ✅ ActionComposer routing | ✅ Same |
| **Privacy Surface** | Minimal | Standard | Larger (new state file) |
| **Maintenance Burden** | Low | Medium | High |
| **Time to Value** | 1 day | 2-3 days | 5+ days |
| **Extensibility (Phase 2)** | Needs rewrite | Natural extension | Natural extension |

**Decision: Option B** — Connector + Skill. This follows the exact pattern of every
other high-value data source (email → connector, subscription monitor → skill). It
provides deterministic alerting without over-engineering into a separate domain.

### 2.3 Component Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  HOME ASSISTANT (192.168.1.123:8123)                                   │
│  28 devices · 6 ecosystems · Matter/Thread · REST API                  │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ GET /api/states (Bearer token, LAN only)
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  CONNECTOR: scripts/connectors/homeassistant.py                        │
│  fetch() → Iterator[Dict]  (entity_id, state, attributes, last_changed)│
│  health_check() → bool     (GET /api/ → 200 OK)                       │
│  Output: stdout (JSONL) + tmp/ha_entities.json (side-effect for skill) │
│  Config: config/connectors.yaml → homeassistant entry                  │
│  Auth: keyring → "artha-ha-token" (long-lived access token)            │
│                                                                        │
│  LAN self-gate: _is_on_lan() in fetch() (NOT in pipeline.py)          │
│  Failure mode: non-blocking (like WorkIQ — catch-up continues)         │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ JSONL entity records
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  SKILL: scripts/skills/home_device_monitor.py                          │
│  pull()  → reads tmp/ha_entities.json (connector side-effect output)   │
│  parse() → deterministic threshold evaluation:                         │
│    • Device offline >2h → DomainSignal(signal_type="device_offline")   │
│    • Energy spike >30% → DomainSignal(signal_type="energy_anomaly")    │
│    • Security device offline → CRITICAL alert                          │
│    • Printer consumable <20% → DomainSignal(signal_type="supply_low")  │
│    • Automation failure → alert                                        │
│  NEW PATTERN: Skill constructs DomainSignal objects directly (§3.7a)   │
│  compare_fields: @property returning field name list (not class attr)  │
│  Factory: get_skill(artha_dir) required by skill_runner.py             │
│  Output: DomainSignal objects → ActionComposer (bypass LLM mediation)  │
│  Cadence: every_run (entity states are ephemeral)                      │
└──────────────────────┬──────────────────────────────────────────────────┘
                       │ DomainSignal (device_offline, energy_anomaly, etc.)
                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  EXISTING: ActionComposer → ActionQueue → TrustEnforcer → Executor     │
│  New signal routing entries in _SIGNAL_ROUTING:                         │
│    device_offline     → instruction_sheet (friction: standard)         │
│    energy_anomaly     → instruction_sheet (friction: low)              │
│    security_offline   → instruction_sheet (friction: high)             │
│    supply_low         → instruction_sheet (friction: low)              │
│    spa_maintenance    → instruction_sheet (friction: standard)         │
│  Phase 2: device_control → ha_service_call (friction: standard)        │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Data Flow Sequence (per catch-up)

```
Step 0:  preflight.py → check HA reachability (P1 non-blocking)
Step 4a: pipeline.py → homeassistant connector fetch() → stdout JSONL + tmp/ha_entities.json
Step 4b: skill_runner.py → home_device_monitor.pull() reads tmp/ha_entities.json → parse() → DomainSignals
           ⚠ Step 4b MUST NOT begin until Step 4a completes (see §3.1a ordering constraint).
Step 7:  Process home domain → update state/home_iot.md (AI appends to iot_energy.history[])
Step 8:  Cross-domain reasoning (LLM-driven compound signals — Wave 3):
           - Travel upcoming + security camera offline → 🔴 CRITICAL
           - Energy spike + weather extreme → compound signal
           - Presence "everyone home" + family dinner time → informational
Step 11: Briefing includes IoT subsection under Home domain
Step 12.5: skill_runner.py → _route_deterministic_signals() → ActionComposer.compose() (Python, no AI)
Step 14: Email briefing includes IoT alerts if ≥🟠
Step 16: Health-check records HA connector metrics
Step 18: Delete tmp/ha_entities.json (ephemeral)
Step 20: channel_push → Telegram includes IoT alerts if ≥🟠
```

**Ordering Constraint (Steps 4a → 4b):**

`pipeline.py` runs connectors with `ThreadPoolExecutor(max_workers=8)`.
`skill_runner.py` runs skills with `ThreadPoolExecutor(max_workers=5)`.
These two executors run in sequence — `pipeline.py` completes first, then
`skill_runner.py` starts (see `artha.py` main loop ordering). Therefore,
`tmp/ha_entities.json` is fully written before any skill reads it. No
additional synchronization is needed. If this ordering is ever changed to
parallel execution, a sentinel pattern (`tmp/ha_entities.json.ready`) MUST
be added.

---

## §3 — Detailed Implementation Plan (Phase C: Incremental Migration)

### Wave 1: Read-Only Connector (Foundation)

**Goal:** Fetch HA entity states during pipeline execution. No analysis, no
alerting — just data availability.

**Estimated Scope:** ~250 LOC production + ~200 LOC tests

#### 3.1 Connector: `scripts/connectors/homeassistant.py`

```python
"""
scripts/connectors/homeassistant.py — Home Assistant connector for Artha.

Fetches entity states from a Home Assistant instance via its REST API.
Local LAN only — no cloud relay. Token stored in system keyring.

Connector registry entry (config/connectors.yaml → homeassistant):
  homeassistant:
    type: iot
    provider: homeassistant
    enabled: false  # Enable after setup
    requires_lan: true

Output record format:
  - entity_id:      "sensor.brother_printer_toner"
  - state:          "40"
  - attributes:     {friendly_name: "Brother Toner Level", unit: "%", ...}
  - last_changed:   "2026-03-19T14:23:45+00:00"
  - domain:         "sensor" (HA domain, not Artha domain)
  - source:         "homeassistant"

Ref: PRD F7.4, Tech Spec §3.5
"""
```

**Implementation details:**

```python
# Module-level constants
_DEFAULT_TIMEOUT = 10  # seconds per API call
_MAX_ENTITIES = 500    # safety cap (typical HA instance: 50-200 entities)
_EXCLUDED_DOMAINS = frozenset({
    "camera", "media_player",  # No audio/video ingestion (privacy)
    "tts", "stt",              # No speech ingestion
    "conversation",            # No HA conversation data
    "persistent_notification", # HA internal
    "update",                  # HA internal update tracking
})

def fetch(*, since: str, max_results: int, auth_context: dict,
          source_tag: str = "homeassistant", **kwargs) -> Iterator[dict]:
    """Fetch entity states from HA REST API.
    
    Args:
        since:        Ignored for entity state (always current).
                      Used for history endpoint if include_history=True.
        max_results:  Cap on returned entities (default: 500).
        auth_context: Must contain 'api_key' key (HA long-lived access token,
                      loaded by load_auth_context() via load_api_key() — see §3.1b).
        **kwargs:     include_domains (list[str]) — filter to these HA domains.
                      exclude_domains (list[str]) — override _EXCLUDED_DOMAINS.
                      include_history (bool) — also fetch /api/history/period.
    
    Yields:
        Dicts: {entity_id, state, attributes, last_changed, domain, source}
    
    Failure mode: Non-blocking. If HA unreachable, yields nothing + logs error.
    """
    # 1. Validate auth_context
    # 2. GET /api/states with Bearer token
    # 3. Filter by domain (exclude cameras, media, etc.)
    # 4. Yield normalized records
    # 5. Optionally fetch /api/history/period for delta analysis
    # 6. Write all yielded records to tmp/ha_entities.json (side effect)
    #    This temp file is the data handoff to home_device_monitor skill.
    #    See §3.1a: Connector → Skill Data Handoff Contract.

def health_check(auth_context: dict) -> bool:
    """Test HA API reachability + token validity.
    
    Calls GET /api/ (lightweight endpoint).
    Returns True if 200 + {"message": "API running."}.
    Returns False on any error (timeout, auth failure, network).
    Does not raise exceptions.
    """
```

**LAN detection strategy:**

```python
def _is_on_lan(ha_url: str) -> bool:
    """Check if HA instance is reachable on LAN.
    
    Strategy:
    1. Parse hostname from ha_url
    2. If hostname is a private IP (RFC 1918: 10.x, 172.16-31.x, 192.168.x):
       - Attempt TCP connect to port (timeout=2s)
       - Return True if connected
    3. If hostname is a public URL or Nabu Casa relay:
       - Return True (let health_check validate auth)
    4. Timeout or connection refused → Return False
    
    This does NOT use ICMP ping (requires root on some platforms).
    """
```

**Security controls:**
- Token loaded via `auth_context["api_key"]` — populated by `load_auth_context()` which calls
  `load_api_key("artha-ha-token")` from the system keyring (see §3.1b auth contract fix)
- Token NEVER written to config files, JSONL output, state files, or logs
- Entity names pass through PII guard before writing to state/home_iot.md
- IP addresses (device tracker entities) are redacted: store only `home`/`not_home`/`unknown`
- Camera entities (domain=camera) are completely excluded from fetch

**LAN self-gating (inside connector):**

The connector implements LAN detection internally in `fetch()`, NOT in
`pipeline.py`'s `_enabled_connectors()`. This is the correct pattern because:
1. `_enabled_connectors()` currently checks only `enabled` and `source_filter` —
   it has NO platform or network gating logic.
2. WorkIQ and other platform-gated connectors handle self-gating inside their
   own `fetch()` implementations (returning empty on platform mismatch).
3. Putting LAN detection in the connector keeps the concern self-contained and
   avoids introducing a new pipeline-level abstraction affecting all connectors.

```python
def fetch(*, since, max_results, auth_context, source_tag="homeassistant", **kwargs):
    # Step 0: Read ha_url from connector config (lives in connectors.yaml → fetch → ha_url)
    ha_url = kwargs.get("ha_url", "")
    if not ha_url:
        logging.warning("[homeassistant] ha_url not configured — run setup_ha_token.py")
        return
    # Step 1: LAN self-gate
    if not _is_on_lan(ha_url):
        logging.info("[homeassistant] Not on home LAN — skipping fetch")
        return  # yields nothing; catch-up continues without IoT data
    # Step 2: Read token from auth_context (populated by load_auth_context → load_api_key)
    token = auth_context.get("api_key")
    if not token:
        logging.error("[homeassistant] No API key in auth_context — run setup_ha_token.py")
        return
    # ... rest of fetch logic (GET /api/states with Bearer token)
```

> **Note on `requires_lan` in connectors.yaml:** The `requires_lan: true` field
> is documentation-only metadata. The enforcement is in the connector code, not
> in pipeline.py's filter logic. This follows the existing self-gating pattern.

> **VPN edge case (M1):** A corporate VPN may route 192.168.x.x traffic to the
> VPN gateway's LAN, not your home LAN. The TCP connect probe would succeed but
> reach the wrong network. This resolves naturally: `health_check()` with the
> actual HA auth token will fail (wrong HA instance or no HA at all), causing
> `fetch()` to yield nothing. No special handling required.

#### 3.1b Auth & URL Contract (Gap Resolutions G1 + G2)

**Context:** Two design gaps were identified during implementation readiness review
(2026-03-20). Both are resolved here with implementation-ready decisions.

**G1 — Auth token path (RESOLVED: fix `load_auth_context` for `api_key` method)**

The `api_key` branch in `scripts/lib/auth.py` `load_auth_context()` currently returns
a stub: `{"provider": "homeassistant", "method": "api_key"}` — no actual credential.
Every existing `api_key` connector (Canvas LMS) bypasses `auth_context` and loads its
own token from the keyring directly. This is a workaround, not an architecture.

**Decision:** Fix `load_auth_context()` to actually load the credential for `api_key` method:

```python
# In scripts/lib/auth.py, load_auth_context(), api_key branch:
# BEFORE (stub):
#   return {"provider": provider, "method": "api_key"}
# AFTER (loads credential from keyring):
elif method == "api_key":
    credential_key = auth_config.get("credential_key", "")
    if not credential_key:
        raise RuntimeError(f"Connector {provider}: api_key method requires 'credential_key' in auth config")
    api_key = load_api_key(credential_key)  # keyring.get_password()
    return {"provider": provider, "method": "api_key", "api_key": api_key}
```

**Impact:** This is a one-function change in `lib/auth.py`. Canvas LMS connector is
unaffected (it ignores `auth_context` and loads its own keys for multi-child support).
All future `api_key` connectors benefit from a working contract. The HA connector reads
`auth_context["api_key"]` and uses it as `Authorization: Bearer {api_key}` header.

**Regression test:** `test_load_auth_context_api_key_loads_credential()` — verify the
`api_key` branch returns the actual keyring credential, not just a stub dict.

**G2 — `ha_url` ownership (RESOLVED: URL lives in `connectors.yaml`)**

The spec previously placed `ha_url` in `user_profile.yaml` under `integrations.homeassistant`.
This contradicts how every single-endpoint connector works in Artha:

- ADO: URL in `connectors.yaml → fetch → base_url`
- RSS: URL in `connectors.yaml → fetch → feeds`
- NOAA: URL derived from `connectors.yaml → fetch → station_id`

Canvas is the exception (URL in `user_profile.yaml`) because Canvas has per-child
instances — the URL is bound to a family member, not a deployment. HA has exactly one
instance on the LAN — it's deployment topology, not user identity.

**Decision:** `ha_url` lives in `connectors.yaml → homeassistant → fetch → ha_url`.
The `setup_ha_token.py` script writes it there during setup. The connector reads it
from its own config dict via `kwargs["ha_url"]` (pipeline.py passes fetch config as kwargs).

**§4.1 Config Ownership Contract update:**

| File | Owns | Rationale |
|------|------|-----------|
| `config/connectors.yaml` | `ha_url`, handler path, fetch defaults, timeout, retry, `exclude_domains`, output format | Technical wiring + deployment topology. URL is infrastructure, not identity. |
| `config/user_profile.yaml` | Consent flags (`presence_tracking`, `presence_adults`, `presence_minors`), `energy_monitoring`, `device_monitoring`, `entity_allowlist`, `entity_blocklist`, `cloud_relay`, `critical_device_overrides` | User consent and personalization. No infrastructure settings. |
| `config/artha_config.yaml` | Feature-flag kill switches only | Runtime toggles. |

#### 3.1a Connector → Skill Data Handoff Contract

**Critical design note:** `pipeline.py` streams JSONL to **stdout** — it does NOT
write per-connector output files to disk. The `skill_runner.py` runs skills in a
**separate execution context** — skills call `pull()` to fetch their own data.

The HA connector bridges this gap by writing its own temp file as a side effect:

```python
# Inside homeassistant.fetch(), after yielding all records:
_ENTITY_CACHE = Path(os.environ.get("ARTHA_DIR", ".")) / "tmp" / "ha_entities.json"

def fetch(*, since, max_results, auth_context, source_tag="homeassistant", **kwargs):
    records = []
    for entity in filtered_entities:
        record = _normalize_entity(entity)
        records.append(record)
        yield record  # → pipeline stdout (standard connector contract)
    # Side effect: write temp file for skill consumption (atomic)
    _ENTITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _tmp = _ENTITY_CACHE.with_suffix(".json.tmp")
    _tmp.write_text(json.dumps(records, default=str))
    os.replace(_tmp, _ENTITY_CACHE)  # atomic on POSIX; near-atomic on Windows
```

**Contract:**
- Writer: `homeassistant.fetch()` writes `tmp/ha_entities.json` atomically
  (write to `.tmp` then `os.replace()`). This prevents partial reads if the
  process is interrupted mid-write.
- Reader: `home_device_monitor.pull()` reads `tmp/ha_entities.json`.
  `pull()` MUST catch `json.JSONDecodeError` and treat it as missing file
  (return empty list) to handle any residual non-atomic edge case.
- Lifecycle: File is ephemeral — deleted at Step 18 cleanup (same as all tmp/ files).
- Missing file: If connector didn't run (off-LAN, disabled, error), skill's `pull()`
  returns empty list and `parse()` produces zero signals. This is graceful degradation.

This is a deliberate design coupling between the HA connector and its companion
skill. It is documented here rather than hidden because it is the ONLY connector
with a companion skill that reads its output. See §0 ADR for escalation trigger.

**Privacy contract for temp artifact (critical):**

The `tmp/` directory is `.gitignore`d but lives inside the OneDrive-synced workspace.
A raw HA state dump can contain family names in `friendly_name`, location semantics
in `device_tracker.*`, and household topology in entity relationships. Therefore:

1. **Sanitize before write, not after read.** The connector MUST apply filtering
   before writing `tmp/ha_entities.json` — not defer sanitization to the skill.
2. **Filtering applied in `fetch()` before temp write:**
   - `_EXCLUDED_DOMAINS` entities never written (camera, media_player, tts, etc.).
   - `device_tracker.*` attributes stripped to `{state: home|not_home|unknown}` only.
     GPS coordinates, zones, latitude, longitude, source_type NEVER touch disk.
   - `friendly_name` passed through `pii_guard.filter_text()` before write.
   - IP address attributes (`ip`, `local_ip`, `network_ip`) stripped.
   - Entity blocklist applied before write.
3. **Temp file contains only normalized, sanitized records.** The skill reads
   pre-sanitized data — it does NOT need to re-filter.
4. **No raw HA response cached.** The full `/api/states` response is held in
   memory only during `fetch()` iteration and discarded after filtering.
5. **OneDrive sync prevention:** The setup script (`scripts/setup_ha_token.py`)
   MUST create `tmp/.nosync` during initialization. This prevents macOS from
   syncing ephemeral files to OneDrive. On non-macOS platforms, this file is
   harmless. Step 18 cleanup deletes the entity cache but preserves `.nosync`.

#### 3.2 Connector Registration: `config/connectors.yaml`

```yaml
  # ── IoT / Smart Home ──────────────────────────────────────────────────────

  homeassistant:
    type: iot
    provider: homeassistant
    enabled: false   # Enable after: python scripts/setup_ha_token.py
    description: "Home Assistant — local smart home hub (LAN only)"
    requires_lan: true
    auth:
      method: api_key
      credential_key: "artha-ha-token"
      setup_script: "scripts/setup_ha_token.py"
    fetch:
      handler: "scripts/connectors/homeassistant.py"
      ha_url: ""                          # Set by setup_ha_token.py (e.g. http://192.168.1.123:8123)
      timeout_seconds: 10
      default_max_results: 500
      include_history: false              # Enable for energy tracking (Phase 1b)
      exclude_domains:                    # ADDITIVE exclusions (on top of hard-coded floor)
        - camera                          # Hard floor: _EXCLUDED_DOMAINS in code (cannot un-exclude)
        - media_player                    # Hard floor
        - tts                             # Hard floor
        - stt                             # Hard floor
        - conversation                    # Hard floor
        - persistent_notification         # Hard floor
        - update                          # Hard floor
        # User can ADD domains here (e.g. "number", "input_boolean")
        # but CANNOT remove hard-floor domains — code enforces _EXCLUDED_DOMAINS
        # regardless of this YAML list. See §5.1 dual-exclusion contract.
      entity_allowlist: []                # Empty = all non-excluded entities (new pattern — no precedent in existing connectors)
      entity_blocklist: []                # Explicit per-entity exclusions (new pattern)
    output:
      format: jsonl
      fields: [entity_id, state, attributes, last_changed, domain, source]
      source_tag: "homeassistant"
    health_check: true
    retry:
      max_retries: 2
      base_delay: 1.0
      max_delay: 5.0
    mcp:
      prefer_mcp: false   # PRIVACY: local API only — no cloud relay
      fallback: "scripts/pipeline.py --source homeassistant"
```

#### 3.3 Pipeline Registration: `scripts/pipeline.py`

Add to `_HANDLER_MAP`:

```python
    # IoT connectors (local LAN — opt-in)
    "connectors.homeassistant": "connectors.homeassistant",
```

> **Note (H5):** `_ALLOWED_MODULES` is derived automatically from `_HANDLER_MAP.values()`
> at module load time (`_ALLOWED_MODULES: frozenset[str] = frozenset(_HANDLER_MAP.values())`).
> Adding an entry to `_HANDLER_MAP` automatically allowlists the module for dynamic import.
> No separate `_ALLOWED_MODULES` edit is needed.

#### 3.4 Auth Setup Script: `scripts/setup_ha_token.py`

```python
"""
scripts/setup_ha_token.py — Setup script for Home Assistant long-lived access token.

Interactive setup:
1. Prompt for HA URL (default: http://192.168.1.123:8123)
2. Prompt for long-lived access token (from HA UI → Profile → Long-Lived Access Tokens)
3. Validate: GET /api/ with token → expect 200
4. Store token in system keyring as "artha-ha-token"
5. Update connectors.yaml → homeassistant → fetch → ha_url with validated URL
6. Set homeassistant → enabled: true
7. Create tmp/.nosync (prevent OneDrive sync of ephemeral files)
8. Print success + next steps

Security:
- Token NEVER written to any file (only keyring)
- Input token masked during entry (getpass)
- Token validated before storage (prevent storing bad tokens)
- Token overwritten in memory before del: `token = "x" * len(token); del token`
  (Note: Python `del` removes the name binding but does NOT zero underlying memory.
  The overwrite-before-del is best-effort defense-in-depth. The real protection is
  that the token is only in memory during this brief interactive script, and the
  persistent store is the OS keyring which provides OS-level memory protection.)
"""
```

#### 3.5 Preflight Integration: `scripts/preflight.py`

Add P1 (non-blocking) check:

```python
def check_ha_connectivity() -> CheckResult:
    """P1 check: Home Assistant reachability.
    
    Conditions:
    - Only runs if connectors.yaml → homeassistant → enabled: true
    - Attempts health_check() on HA connector
    - P1 (non-blocking): failure logs warning but does NOT halt catch-up
    
    Returns:
    - passed: HA reachable and token valid
    - warning: HA unreachable (LAN issue) or token expired
    - skipped: HA connector not enabled
    """
```

#### 3.6 Platform Gating

The HA connector introduces a new platform constraint: `requires_lan: true`.

**Behavior:**
- If `requires_lan: true` and machine is NOT on the same LAN as HA:
  - Skip connector silently (like WorkIQ on Mac — no error, no warning)
  - If stale `tmp/ha_entities.json` exists (<12h): surface metadata summary
  - If no cached data: "🏠 Smart home data unavailable (not on home LAN)"
- If running in Cowork VM (read-only environment):
  - HA will likely be unreachable (VM has no LAN access)
  - Same graceful degradation as all VM-blocked connectors

**Existing pattern justification:** WorkIQ bridge already handles platform-gating
(`platform: windows`) via self-gating inside its own connector code. The HA connector
follows the same self-gating model: LAN detection runs inside `fetch()`, not in
`pipeline.py`'s `_enabled_connectors()` (which only checks `enabled` and `source_filter`).
See §3.1a for the full self-gating contract.

---

### Wave 2: Deterministic Skill (Intelligence)

**Goal:** Analyze HA entity data with deterministic thresholds. Emit structured
alert data that the catch-up orchestrator converts into `DomainSignal` objects
for the action pipeline.

**Estimated Scope:** ~300 LOC production + ~250 LOC tests

#### 3.7a Signal Creation Contract

**Who creates `DomainSignal` objects?**

The skill itself. This is a **new pattern** — existing skills return plain dicts
from `execute()` and the AI reads them during Steps 8-11 to construct signals.
The HA skill departs from this by importing `DomainSignal` and constructing
signal objects directly in `parse()`. This is a deliberate design choice:

- **Deterministic principle:** If the AI is responsible for constructing signals
  from skill data, we introduce an LLM-dependent step that could miss or
  misinterpret alerts. A security camera offline for 3 hours should ALWAYS
  produce a signal — not "usually" produce one.
- **ActionComposer compatibility:** `ActionComposer.compose()` accepts
  `DomainSignal` objects. The skill constructs them; the orchestrator passes
  them directly to the composer without LLM mediation.
- **Pattern acknowledgment:** This is explicitly noted as a new pattern that
  other future skills may adopt when deterministic alerting is required.

**Data flow (corrected):**
```
homeassistant.fetch() → yields JSONL to stdout + writes tmp/ha_entities.json
home_device_monitor.pull() → reads tmp/ha_entities.json
home_device_monitor.parse() → deterministic thresholds → constructs DomainSignal objects
home_device_monitor.execute() → returns dict with "signals": [DomainSignal, ...]
skill_runner.py → caches result in skills_cache.json
catch-up orchestrator (Step 12.5) → reads signals from cache → ActionComposer.compose()
```

The orchestrator must be aware that `home_device_monitor` output contains
pre-built `DomainSignal` objects under the `"signals"` key. These bypass
the normal "AI constructs signals" flow and go directly to `ActionComposer`.

#### 3.7b Signal Serialization Contract

`DomainSignal` is a frozen dataclass — it is NOT JSON-serializable by default.
`skill_runner.py` caches skill results in `skills_cache.json` as JSON. The
serialization/deserialization path must be explicitly defined:

**Serialization (skill → cache):**
```python
# In HomeDeviceMonitorSkill.execute():
import dataclasses

def execute(self) -> Dict[str, Any]:
    raw = self.pull()
    parsed = self.parse(raw)
    # Serialize DomainSignal objects to plain dicts for JSON cache
    parsed["signals"] = [dataclasses.asdict(s) for s in parsed.get("signals", [])]
    return {
        "name": "home_device_monitor",
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": parsed,
    }
```

**Deserialization (cache → ActionComposer):**

`skill_runner.py` is extended to detect the `"signals"` key in cached skill
results and re-hydrate them as `DomainSignal` objects for `ActionComposer`:

```python
# In skill_runner.py, after loading skills_cache.json:
from actions.base import DomainSignal

def _extract_signals(skill_result: dict) -> list[DomainSignal]:
    """Re-hydrate serialized signal dicts into DomainSignal objects."""
    raw_signals = skill_result.get("data", {}).get("signals", [])
    return [DomainSignal(**s) for s in raw_signals]
```

This runs as Python code inside `skill_runner.py` — NOT as LLM-mediated reasoning.
The signal routing path is: `skill_runner.py` → `_extract_signals()` → `ActionComposer.compose()`.
No AI step is required between skill output and action composition for deterministic signals.

**Contract:**
- `execute()` MUST serialize signals via `dataclasses.asdict()` before returning.
- `skill_runner.py` MUST re-hydrate via `DomainSignal(**dict)` before passing to composer.
- Round-trip fidelity: `DomainSignal(**dataclasses.asdict(signal)) == signal` (guaranteed
  by frozen dataclass with all-primitive fields).
- Test: `test_signal_round_trip_serialization()` verifies lossless round-trip.

#### 3.7c Signal Routing via skill_runner.py (Step 12.5 Execution Path)

**Problem:** §2.4 Step 12.5 says "Orchestrator reads skill cache → extracts
pre-built DomainSignals → ActionComposer.compose()." But the "orchestrator"
at Steps 8–12 is the LLM/AI — it cannot call Python functions directly.

**Resolution:** Signal routing is performed by `skill_runner.py` as a Python
code path, NOT by the AI orchestrator:

```python
# In skill_runner.py, after all skills complete:
def _route_deterministic_signals(skills_cache: dict, artha_dir: Path) -> None:
    """Route pre-built DomainSignals to ActionComposer.
    
    Runs after all skills execute. Checks each skill result for a "signals"
    key and routes any found signals directly to ActionComposer.compose().
    This bypasses the AI orchestrator entirely — deterministic signals are
    guaranteed to reach the action queue regardless of LLM behavior.
    """
    from action_composer import ActionComposer
    composer = ActionComposer(artha_dir)
    for skill_name, result in skills_cache.items():
        signals = _extract_signals(result)
        for signal in signals:
            composer.compose(signal)
```

**Execution order:**
1. `skill_runner.py` executes all skills (including `home_device_monitor`).
2. `skill_runner.py` calls `_route_deterministic_signals()` — signals flow
   to `ActionComposer` → `ActionQueue` as Python code.
3. AI orchestrator (Steps 8–12) processes skill cache for briefing content.
   The AI does NOT need to construct signals — they're already queued.
4. AI may still reference signal data for briefing formatting ("Ring camera
   offline 3h" in the briefing text), but the signal itself is guaranteed.

This keeps the deterministic principle intact: threshold evaluation, signal
construction, serialization, deserialization, and action routing are all
Python code paths. No LLM inference in the critical path.

#### 3.7 Skill: `scripts/skills/home_device_monitor.py`

```python
"""
scripts/skills/home_device_monitor.py — Home device monitoring skill.

Reads cached HA entity data (from connector temp file) and applies deterministic
threshold checks. Constructs DomainSignal objects directly (new pattern — see §3.7a).

Registered in config/skills.yaml (cadence: every_run).
Ref: PRD F7.4, F12.5
"""
from actions.base import DomainSignal  # New: skill constructs signals directly

class HomeDeviceMonitorSkill(BaseSkill):
    """Deterministic device monitoring with threshold-based alerting."""
    
    # compare_fields is an abstract @property in BaseSkill (not a class attribute).
    # Every existing skill implements it as @property. Follow the same pattern.
    
    @property
    def compare_fields(self) -> list[str]:
        return [
            "offline_count",
            "critical_offline_count",
            "energy_anomaly_count",
            "supply_low_count",
            "automation_failure_count",
        ]
    
    # ── Device classification ────────────────────────────────────────────
    
    # Critical devices: offline triggers 🔴 alert
    _CRITICAL_DEVICES = {
        "binary_sensor.ring_*",                # All Ring sensors (motion, status, connectivity)
        "sensor.ring_*",                       # All Ring attributes (battery, signal strength, etc.)
        "alarm_control_panel.*",               # Security system
        "lock.*",                              # Smart locks
    }  # CQ-HA6 RESOLVED: Ring=CRITICAL confirmed 2026-03-20
    
    # Monitored devices: offline triggers 🟠 alert
    _MONITORED_DEVICES = {
        "light.*",                             # Smart lights (confirmed MONITORED by user, 2026-03-20)
        "switch.*",                            # Smart switches/plugs
        "sensor.brother_*",                    # Brother printer (defaulted MONITORED — not explicitly classified)
        "climate.*",                           # HVAC
        "water_heater.*",                      # Water heater
        "sensor.gecko_*",                      # Swim spa (if Gecko integration configured in HA)
    }  # CQ-HA6 RESOLVED: lights confirmed MONITORED (promoted from INFORMATIONAL)
    
    # Implementation note: _CRITICAL_DEVICES and _MONITORED_DEVICES use glob-style
    # wildcards ("binary_sensor.ring_*"). Entity matching MUST use fnmatch.fnmatch()
    # from the Python stdlib — plain string comparison won't match these patterns.
    # Example: fnmatch.fnmatch("binary_sensor.ring_floodlight_motion", "binary_sensor.ring_*") → True
    
    # Informational: offline triggers 🟡 alert (no action proposal)
    # Everything else that's not excluded
    
    # ── Threshold constants ──────────────────────────────────────────────
    
    OFFLINE_THRESHOLD_HOURS = 2        # Device unreachable for >2h
    ENERGY_SPIKE_PCT = 30              # >30% above 7-day average
    SUPPLY_LOW_PCT = 20                # Printer toner/drum <20%
    SPA_TEMP_VARIANCE_F = 5            # >5°F deviation from set point
    
    def pull(self) -> Any:
        """Read cached HA entity data from tmp/ha_entities.json.
        
        Returns parsed JSON list, or empty list if file missing (connector
        didn't run — off-LAN, disabled, error). See §3.1a handoff contract.
        """
        
    def parse(self, raw_data: Any) -> Dict[str, Any]:
        """Apply deterministic threshold checks.
        
        Constructs DomainSignal objects directly (new pattern — see §3.7a).
        
        Returns:
            {
                "offline_count": int,
                "critical_offline_count": int,
                "energy_anomaly_count": int,
                "supply_low_count": int,
                "automation_failure_count": int,
                "offline_devices": [...],
                "critical_offline": [...],
                "energy_anomalies": [...],
                "supply_alerts": [...],
                "automation_failures": [...],
                "spa_status": {...},
                "presence": {...},           # Consent-gated
                "ha_system_health": {...},
                "signals": [DomainSignal, ...]  # Pre-built; bypass LLM mediation
            }
        """
        
    def execute(self) -> Dict[str, Any]:
        """Orchestrate pull + parse. Standard BaseSkill pattern.
        
        Returns (cached in skills_cache.json by skill_runner.py):
            {
                "name": "home_device_monitor",
                "status": "success" | "failed",
                "timestamp": ISO-8601,
                "data": {  # output of parse()
                    "offline_count": int,
                    "critical_offline_count": int,
                    ... (all fields from parse() above)
                    "signals": [DomainSignal, ...]  # serialized for cache
                }
            }
        """
        
    def to_dict(self) -> Dict[str, Any]:
        """Return serializable skill metadata for skills_cache.json."""


def get_skill(artha_dir: "Path") -> HomeDeviceMonitorSkill:
    """Factory function called by skill_runner.py.
    
    Every skill module MUST expose this function. skill_runner.py calls
    `module.get_skill(artha_dir)` to instantiate the skill.
    Without this, skill_runner.py raises AttributeError.
    """
    return HomeDeviceMonitorSkill(artha_dir)
```

**Threshold evaluation logic:**

```python
def _check_device_offline(self, entities: list[dict]) -> list[dict]:
    """Check for devices that have been unavailable for >OFFLINE_THRESHOLD_HOURS.
    
    Logic:
    1. For each entity where state == "unavailable" or "unknown":
       a. Parse last_changed timestamp
       b. If now - last_changed > OFFLINE_THRESHOLD_HOURS:
          - Classify: critical (security) vs. monitored vs. informational
          - Emit appropriate alert level
    2. For device_tracker entities (presence):
       - ONLY process if user_profile.yaml → integrations.homeassistant.presence_tracking: true
       - ONLY store home/not_home/unknown (never coordinates or zones)
       - Note: pull() receives device_tracker entities with ONLY their `state` string —
         no attribute data. GPS coordinates, zones, source_type are stripped by the
         connector's privacy filter BEFORE temp file write. See §3.1a privacy contract.
    """

def _check_energy_anomaly(self, entities: list[dict]) -> list[dict]:
    """Detect energy consumption spikes.
    
    Logic:
    1. Read sensor entities with device_class: energy or power
    2. Compare current reading vs. 7-day rolling average from state/home_iot.md → energy_history
    3. If current > average * (1 + ENERGY_SPIKE_PCT/100):
       - Emit energy_anomaly signal
       - Include: entity_id, current_value, average_value, spike_pct
    4. Correlate with weather: if NOAA skill shows extreme temp, add context to signal
    
    Cold-start protocol (H1):
    - Skill writes `iot_energy.daily_kwh` and `current_power_w` every run.
    - AI appends to `iot_energy.history[]` during Step 7 state update.
    - Anomaly detection ONLY activates when `len(history) >= 7` (7+ data points).
    - Days 1-6: energy readings surfaced as informational only (no spike detection).
    - First run with no history: skip anomaly check entirely, return empty list.
    """

def _check_supply_levels(self, entities: list[dict]) -> list[dict]:
    """Check printer consumables and other supply-type sensors.
    
    Logic:
    1. Read sensor entities with device_class: None and unit: "%"
       (common for printer toner, drum level, ink levels)
    2. If int(state) < SUPPLY_LOW_PCT → emit supply_low signal
    3. Special handling for Brother printer:
       - Toner level, drum level, page count
       - Correlate pages_remaining vs. usage rate for depletion estimate
    """

def _check_spa_status(self, entities: list[dict]) -> dict:
    """Monitor Gecko in.touch 2 swim spa.
    
    Logic:
    1. Read sensor entities matching "sensor.gecko_*" or "climate.swim_spa"
    2. Track: water_temp, set_temp, pump_status, filtration_status, error_codes
    3. Alert if:
       - Water temp > set_temp + SPA_TEMP_VARIANCE_F or < set_temp - SPA_TEMP_VARIANCE_F
       - Error code present (HA entity attribute: error_code != None)
       - Filtration hasn't run in >48h (if HA tracks filtration schedule)
    """

def _check_automation_health(self, entities: list[dict]) -> list[dict]:
    """Monitor HA automation execution status.
    
    Logic:
    1. Read automation entities (domain: automation)
    2. Check last_triggered attribute
    3. If automation has schedule but last_triggered is >2× expected interval:
       - Emit automation_failure signal (🟡 informational)
    """
```

#### 3.8 Skill Registration: `config/skills.yaml`

```yaml
  home_device_monitor:
    enabled: false   # Enable after homeassistant connector is configured
    priority: P1
    cadence: every_run   # Documentation-only metadata (like requires_lan).
                                        # skill_runner.py does NOT enforce this field —
                                        # validation is in the skill code (pull() returns
                                        # empty list if connector data is missing).
    execution_after: pipeline            # Skill depends on connector output (§2.4 Step 4a→4b).
                                         # skill_runner.py does not enforce this today —
                                         # sequencing is guaranteed by artha.py main loop
                                         # (pipeline completes before skill_runner starts).
                                         # Recorded here for future parallel execution support.
    requires_vault: false
    safety_critical: false
    requires_connector: homeassistant
    description: "Monitor HA devices: offline alerts, energy anomalies, supply levels, spa status."
```

#### 3.9 Skill Runner Registration: `scripts/skill_runner.py`

Add to `_ALLOWED_SKILLS`:

```python
_ALLOWED_SKILLS: frozenset[str] = frozenset({
    # ... existing skills ...
    # IoT / Smart Home
    "home_device_monitor",
})
```

#### 3.10 Signal Routing: `scripts/action_composer.py`

Add to `_SIGNAL_ROUTING`:

```python
    # IoT / Smart Home signals
    "device_offline": {
        "action_type": "instruction_sheet",
        "friction": "standard",
        "min_trust": 1,
        "reversible": False,
        "undo_window_sec": None,
    },
    "security_device_offline": {
        "action_type": "instruction_sheet",
        "friction": "high",
        "min_trust": 0,   # Always notify for security devices
        "reversible": False,
        "undo_window_sec": None,
    },
    "energy_anomaly": {
        "action_type": "instruction_sheet",
        "friction": "low",
        "min_trust": 1,
        "reversible": False,
        "undo_window_sec": None,
    },
    "supply_low": {
        "action_type": "instruction_sheet",
        "friction": "low",
        "min_trust": 1,
        "reversible": False,
        "undo_window_sec": None,
    },
    "spa_maintenance": {
        "action_type": "instruction_sheet",
        "friction": "standard",
        "min_trust": 1,
        "reversible": False,
        "undo_window_sec": None,
    },
    # Cross-domain compound signal (Wave 3, safety-critical — §3.13)
    "security_travel_conflict": {
        "action_type": "instruction_sheet",
        "friction": "high",
        "min_trust": 0,   # Always notify — safety-critical compound
        "reversible": False,
        "undo_window_sec": None,
    },
```

#### 3.11 Home Domain Enhancement: `prompts/home.md`

Add IoT section:

```markdown
## IoT / Smart Home (Home Assistant Integration)

### Data Source
Home Assistant connector (config/connectors.yaml → homeassistant).
Data arrives as entity states in tmp/ha_entities.json during Step 4.
Skill `home_device_monitor` processes this data during Step 4 skill runner.
IoT state stored in `state/home_iot.md` (machine-owned companion file).

### Extraction Rules (from skill output, not email)
1. **Device Status**: Which devices are online/offline/unavailable
2. **Critical Devices**: Security cameras, locks, alarm → highest priority
3. **Energy**: Current consumption vs. 7-day average; spike detection
4. **Supplies**: Printer toner/drum/ink levels and depletion forecast
5. **Swim Spa**: Water temperature, filtration, error codes
6. **Automations**: Last triggered time vs. expected schedule

### Alert Thresholds
🔴 **CRITICAL**:
- Security device (Ring camera, lock, alarm) offline >2 hours
- HA system itself unreachable for >1 hour during active monitoring
- Swim spa error code present

🟠 **URGENT**:
- Non-security monitored device offline >2 hours
- Energy consumption spike >30% above 7-day average
- Swim spa water temp deviation >5°F from set point

🟡 **STANDARD**:
- Printer consumable <20%
- Automation failed to trigger on schedule
- Informational device offline >2 hours

### State File Update Protocol
Update `state/home_iot.md → iot_devices` and `state/home_iot.md → iot_energy`:

**Write Protocol:** `state/home_iot.md` is machine-owned. All writes MUST
use atomic write (write to `state/home_iot.md.tmp` then `os.replace()` to
final path) to prevent corruption on crash. The net-negative write guard
(§Core) does NOT apply to this file — it is machine-owned data that changes
every catch-up cycle. The AI MUST write the full file content each time (not
append), since the entire state is refreshed from HA.

```yaml
iot_devices:
  last_sync: ISO-8601
  ha_version: "2026.3.2"
  total_entities: N
  online: N
  offline: N
  critical_offline: []    # List of entity_ids
  supply_alerts: []       # {entity_id, level_pct, friendly_name}
  spa:
    water_temp_f: N
    set_temp_f: N
    pump: on|off
    filtration: on|off|unknown
    error: null|"code"
iot_energy:
  last_updated: ISO-8601
  current_power_w: N
  daily_kwh: N
  weekly_avg_kwh: N
  spike_detected: true|false
  spike_pct: N
```

### Briefing Format (IoT subsection under Home)
```
### Home
• 🏠 Smart Home: [N] devices online, [M] offline
  • 🔴 Ring Floodlight (front) offline since [time] — check power/WiFi
  • 🟡 Brother printer: toner 40% (~500 pages remaining)
• ♨️ Swim spa: 102°F (set: 104°F), pump running, no errors
• ⚡ Energy: 45 kWh today (avg: 38 kWh) — +18% [within normal range]
```
```

#### 3.12 Home State Enhancement: `state/home_iot.md`

**Machine-owned companion file** (not appended to `state/home.md`).

`state/home.md` is a human-authored prose/table document (property details,
mortgage, HOA, utilities, insurance, maintenance). Appending machine-generated
YAML sections into this file risks write conflicts, net-negative write guard
trips, and long-term maintainability issues.

Instead, IoT state lives in a dedicated `state/home_iot.md` file that is
exclusively machine-owned. The AI reads both `state/home.md` (human-authored)
and `state/home_iot.md` (machine-generated) when processing the home domain.

Add `state/home_iot.md` (initially empty, populated on first HA-connected catch-up):

```markdown
# Home IoT State (Machine-Managed)
<!-- This file is exclusively written by the home_device_monitor skill.
     Do not edit manually — changes will be overwritten on next catch-up. -->

## IoT Devices

iot_devices:
  last_sync: ""
  ha_version: ""
  total_entities: 0
  online: 0
  offline: 0
  critical_offline: []
  supply_alerts: []
  spa:
    water_temp_f: null
    set_temp_f: null
    pump: unknown
    filtration: unknown
    error: null

## Smart Home Energy

iot_energy:
  last_updated: ""
  current_power_w: 0
  daily_kwh: 0
  weekly_avg_kwh: 0
  spike_detected: false
  spike_pct: 0
  history: []  # Rolling 30-day daily totals — truncate to last 30 entries each write
```

**History Truncation:** On each write, `iot_energy.history` MUST be trimmed
to the most recent 30 entries: `history = history[-30:]`. This prevents
unbounded file growth. If historical energy data beyond 30 days is needed,
it should be queried from HA directly.

**Cross-reference:** `prompts/home.md` IoT section (§3.11) references
`state/home_iot.md` as the data source. The AI reads this file alongside
`state/home.md` during Step 7 home domain processing.

---

### Wave 3: Cross-Domain Intelligence (Compound Signals)

**Goal:** Enable the 10 creative applications identified in the IoT assessment.
These are cross-domain compound signals that leverage existing Step 8 reasoning.

> **Note (C6):** Wave 3 compound signals are inherently **LLM-driven** (heuristic),
> not deterministic. They are expressed as rules in `config/Artha.md` Step 8f, which
> is a prompt file interpreted by the AI during catch-up. This is correct by design:
> multi-domain correlation (travel + security, energy + weather, printer + school deadline)
> requires contextual reasoning that deterministic thresholds cannot capture. Waves 1-2
> provide the deterministic single-domain alerting; Wave 3 adds heuristic cross-domain
> intelligence on top.
>
> **Exception — safety-critical compounds:** Compound signals with safety implications
> (e.g., travel upcoming + security camera offline) MUST NOT depend solely on prompt
> phrasing or model variance. These should be implemented as a small deterministic
> correlator function invoked in the skill runner alongside `home_device_monitor`,
> not as prompt rules. The correlator:
> 1. Reads structured outputs from HA skill (offline devices) + travel skill (upcoming trips).
> 2. Emits typed `DomainSignal(signal_type="security_travel_conflict", urgency=3)`.
> 3. Prompt layer handles formatting and summarization only — the signal is guaranteed.
>
> Non-safety compounds (energy + weather context, family gathering, printer + school)
> remain LLM-driven in Step 8f — model variance is acceptable for informational signals.

**Safety-Critical Correlator — Implementation Home:**

The deterministic correlator for safety-critical compound signals lives in:

**File:** `scripts/skills/security_travel_correlator.py` (~60 LOC)

```python
"""
scripts/skills/security_travel_correlator.py — Deterministic cross-domain correlator.

Reads structured outputs from HA skill (offline security devices) and
travel skill (upcoming trips) in the skills cache. Emits a typed
DomainSignal(signal_type="security_travel_conflict", urgency=3) when
both conditions are met. This is NOT an LLM-driven heuristic — the signal
is guaranteed by Python code.

Registered in: _ALLOWED_SKILLS (skill_runner.py)
Execution order: MUST run after both home_device_monitor and travel skills.
Estimated scope: ~60 LOC production + ~40 LOC tests.
"""
import dataclasses
from pathlib import Path
from actions.base import DomainSignal

def get_skill(artha_dir: Path):
    return SecurityTravelCorrelator(artha_dir)

class SecurityTravelCorrelator(BaseSkill):
    compare_fields = ["security_travel_conflicts"]

    def pull(self) -> dict:
        """Read skills_cache.json for home_device_monitor + travel skill outputs."""
        ...

    def parse(self, raw: dict) -> dict:
        """Cross-reference offline security devices with upcoming travel."""
        ...

    def execute(self) -> dict:
        """Emit security_travel_conflict DomainSignal if conditions met.
        Returns {"signals": [dataclasses.asdict(signal), ...]}
        """
        ...
```

**Registration:** Add `"security_travel_correlator"` to `_ALLOWED_SKILLS` in
`skill_runner.py`. Add to `config/skills.yaml`:

```yaml
  security_travel_correlator:
    enabled: false   # Enable after both home_device_monitor AND travel skills configured
    priority: P1
    cadence: every_run
    requires_vault: false
    safety_critical: true
    execution_after: home_device_monitor  # Must run after its data sources
    description: "Cross-domain: travel upcoming + security camera offline → critical alert"
```

**Estimated Scope:** ~60 LOC production + ~40 LOC tests

#### 3.13 Compound Signal Rules (Step 8f additions)

Add to `config/Artha.md` Step 8f compound signal detection rules:

```
7. Travel booking (from travel domain) + security camera offline (from IoT skill)
   → 🔴 "Security camera [name] is offline — resolve before travel on [date]"
   
8. Energy spike (from IoT skill) + extreme weather (from NOAA skill)
   → 🟡 "Energy +30% — likely weather-driven (high [temp]°F today)"
   
9. Everyone home (from IoT presence, consent-gated) + evening hours
   → Informational: "Family gathering opportunity" (if goals.md tracks family time)
   
10. Printer supply low (from IoT skill) + school deadline approaching (from kids domain)
    → 🟠 "Printer toner at [N]% — [child] has [assignment] due [date]"
    
11. Energy trend rising + utility bill due (from finance domain)
    → 🟡 "Energy up [N]% this month — PSE bill likely higher than $[estimate]"
```

#### 3.14 Creative Applications Mapping

| # | Application | Artha Components Used | New Code |
|---|------------|----------------------|----------|
| 1 | **Security-Aware Travel Intelligence** | travel.md + IoT skill + Step 8f | ~20 LOC (compound signal rule) |
| 2 | **Energy Cost Correlation** | IoT energy + finance.md (PSE) + NOAA weather | ~30 LOC (correlation logic) |
| 3 | **Parental Screen-Time Awareness** | IoT (device power states) + kids.md + calendar | ~20 LOC (compound rule) — Phase 2 |
| 4 | **Proactive Supply Chain** | IoT supply levels + shopping.md | ~15 LOC (shopping list signal) |
| 5 | **Everyone's Home Signal** | IoT presence + goals.md (family time) | ~15 LOC — consent-gated |
| 6 | **Utility Anomaly Detection** | IoT energy history + finance.md (bill history) | ~25 LOC (trend comparison) |
| 7 | **Guest Mode & Party Intelligence** | IoT presence count + calendar events | ~20 LOC — Phase 2 |
| 8 | **Swim Spa Lifecycle Manager** | IoT spa status + home.md (maintenance) | ~25 LOC (maintenance scheduler) |
| 9 | **Home Health Score** | IoT (all devices) + insurance risks + maintenance | ~40 LOC (composite score) |
| 10| **Maintenance Prediction** | IoT (device hours, error codes) + home.md | ~30 LOC — Phase 2 |

---

### Wave 4: Phase 2 — Device Control (Action Layer)

**Goal:** Enable Artha to execute device control actions via HA service calls.
This wave requires its own security review and is **NOT part of the initial release**.

**Estimated Scope:** ~200 LOC production + ~200 LOC tests

> **⚠️ GATE: Wave 4 MUST NOT begin until:**
> 1. Wave 1-3 have been in production for ≥30 days
> 2. Zero false positive security_device_offline signals
> 3. User explicitly approves Phase 2 scope via CQ-HA3
> 4. Physical-security threat model reviewed and accepted

#### 3.15 Action Handler: `scripts/actions/homeassistant_service.py`

```python
"""
scripts/actions/homeassistant_service.py — HA service call action handler.

Executes device control actions via Home Assistant's POST /api/services/{domain}/{service}.
All calls require explicit user approval (autonomy_floor: true).

Implements the ActionHandler protocol as module-level functions, matching the
established pattern (email_send.py, whatsapp_send.py, calendar_create.py, etc.).

Ref: specs/iot.md §3 Wave 4
"""
from actions.base import ActionProposal, ActionResult

# ── Service allowlists ──────────────────────────────────────────────────

_SERVICE_ALLOWLIST = frozenset({
    "switch.turn_on", "switch.turn_off",
    "light.turn_on", "light.turn_off",
    "climate.set_temperature",
})

_ELEVATED_SERVICES = frozenset({
    "lock.lock", "lock.unlock",
    "alarm_control_panel.arm_away",
    "alarm_control_panel.disarm",
})

_BLOCKED_SERVICES_COMMENT = """
BLOCKED services (never callable from Artha):
  - automation.trigger (could cascade unexpected actions)
  - script.* (opaque execution)
  - homeassistant.restart / homeassistant.stop (infrastructure)
  - camera.* (privacy)
  - media_player.* (out of scope)
"""


def validate(proposal: ActionProposal) -> tuple[bool, str]:
    """Pre-execution validation. No side effects, no API calls.
    
    Checks:
    1. service_domain + service_action in _SERVICE_ALLOWLIST or _ELEVATED_SERVICES
    2. entity_id present and non-empty
    3. If elevated service: trust_level >= 2 in proposal metadata
    4. service not in blocked set
    """
    ...


def dry_run(proposal: ActionProposal) -> ActionResult:
    """Simulate the service call. Read-only — calls GET /api/states/{entity_id}
    to verify entity exists and is controllable, but does NOT call the service.
    Returns ActionResult describing what WOULD happen.
    """
    ...


def execute(proposal: ActionProposal) -> ActionResult:
    """Execute the HA service call. THIS IS THE WRITE PATH.
    
    POST /api/services/{domain}/{service} with entity_id in body.
    Requires LAN reachability (or Nabu Casa if cloud_relay: true).
    Timeout: 10s. Idempotent where possible (same entity + service = same result).
    """
    ...


def health_check() -> bool:
    """Verify HA API reachability and token validity.
    Calls GET /api/ — returns True if 200. No side effects.
    """
    ...


def build_reverse_proposal(
    original: ActionProposal, result: ActionResult
) -> ActionProposal | None:
    """Build undo proposal: turn_on → turn_off, lock → unlock, etc.
    Returns None for non-reversible services.
    """
    ...
```

#### 3.16 Action Registration: `config/actions.yaml`

```yaml
  ha_service_call:
    handler: "scripts/actions/homeassistant_service.py"
    enabled: false          # Phase 2 — enable after 30-day read-only period
    friction: standard      # Elevated to 'high' for locks/alarm
    min_trust: 1
    sensitivity: standard   # Elevated to 'high' for locks/alarm
    timeout_sec: 10
    retry: false
    reversible: true        # Most service calls have an inverse
    undo_window_sec: 60
    rate_limit:
      max_per_hour: 20
      max_per_day: 100
    pii_check: false        # Device actions contain no PII
    audit: true
    autonomy_floor: true    # ALWAYS requires human approval
    requires_lan: true      # Can only execute on LAN
```

#### 3.17 Signal Routing (Phase 2 addition): `scripts/action_composer.py`

```python
    # Phase 2: Device control signals
    "device_control": {
        "action_type": "ha_service_call",
        "friction": "standard",
        "min_trust": 1,
        "reversible": True,
        "undo_window_sec": 60,
    },
    "security_control": {
        "action_type": "ha_service_call",
        "friction": "high",
        "min_trust": 2,       # Requires trust level 2 for security actions
        "reversible": True,
        "undo_window_sec": 60,
    },
```

---

## §4 — Distribution & Multi-Household Architecture

### 4.1 User Profile Integration

The HA integration must work seamlessly across all household types.

**Configuration Ownership Contract:**

HA config is split across three files, matching the existing pattern for gmail,
workiq, and other integrations:

| File | Owns | Rationale |
|------|------|-----------|
| `config/connectors.yaml` | `ha_url`, handler path, fetch defaults, timeout, retry, `exclude_domains` hard-floor commentary, output format | Technical wiring + deployment topology. URL is infrastructure metadata (like ADO base_url), not user identity. Written by `setup_ha_token.py`. |
| `config/user_profile.yaml` | Consent flags (`presence_tracking`, `presence_adults`, `presence_minors`), `energy_monitoring`, `device_monitoring`, `entity_allowlist`, `entity_blocklist`, `cloud_relay`, `critical_device_overrides` | User consent and personalization. No infrastructure settings. |
| `config/artha_config.yaml` | Feature-flag kill switches (`connector_enabled`, `skill_enabled`, `compound_signals`, `phase2_control`) | Runtime toggles only. No behavioral config. Matches existing `actions:` feature flags. |

**Rule:** If a setting affects user consent, privacy, or integration identity, it
belongs in `user_profile.yaml`. If it affects deployment topology, handler wiring,
or retry policy, it belongs in `connectors.yaml`. If it's a kill switch, it belongs
in `artha_config.yaml`. No setting should appear in more than one file.

**`config/user_profile.yaml` additions:**

```yaml
integrations:
  homeassistant:
    presence_tracking: false            # Opt-in (CQ-HA2)
    presence_adults: false              # Track adult family members
    presence_minors: false              # Track children (higher bar)
    energy_monitoring: true             # Default: on (no privacy concern)
    device_monitoring: true             # Default: on
    phase2_device_control: false        # Explicit opt-in for Phase 2
    entity_allowlist: []                # User-customized entity filter
    entity_blocklist: []                # User-customized entity exclusion
    cloud_relay: false                  # Set true only if Nabu Casa acknowledged
    critical_device_overrides: {}       # User can reclassify device criticality
```

> **Note:** `ha_url` and `enabled` are in `connectors.yaml`, not here.
> See §3.1b (G2) for the rationale.

**Household type adaptation:**

| `household.type` | HA Behavior |
|-------------------|-------------|
| `single` | Presence tracking simplified (home/away). Screen-time compound signals suppressed. |
| `couple` | Presence for 2 adults. No kids compound signals. |
| `family` | Full presence (consent per member). Kids screen-time enabled. Family gathering detection. |
| `multi_gen` | Same as family. Elderly fall-detection entities surfaced if HA tracks them. |
| `roommates` | Presence per person (individual consent). Shared utility cost splitting in energy signals. |

**Renter mode adaptation:**

| `household.tenure` | HA Behavior |
|---------------------|-------------|
| `renter` | Suppress home value signals. Energy focus shifts to "manage cost" not "invest in solar". No HVAC maintenance forecasts (landlord responsibility). |
| `owner` | Full feature set. Include maintenance prediction, home health score. |

### 4.2 Cross-Platform Behavior

| Platform | HA Connector Behavior |
|----------|----------------------|
| macOS (home LAN) | Full operation — fetch entities, run skill, emit signals |
| macOS (off LAN) | Silent skip. Surface: "🏠 Not on home LAN — IoT data from last sync: [time]" |
| Windows (home LAN) | Full operation (same as macOS) |
| Windows (off LAN) | Silent skip (same as macOS off-LAN) |
| Linux | Full operation (Artha runs on Linux) |
| Cowork VM | Skip (no LAN access). Note in footer: "IoT data unavailable (VM environment)" |

---

## §5 — Security & Privacy Architecture

### 5.1 Non-Negotiable Security Principles

| Principle | Implementation |
|-----------|---------------|
| **LAN-only by default** | Token never sent to cloud endpoints. `ha_url` validated as private IP or user-confirmed safe URL. If Nabu Casa relay is configured (public URL), user must explicitly acknowledge cloud transit. |
| **Token in keyring only** | Long-lived access token stored via Python `keyring` library (macOS Keychain / Windows Credential Manager / Linux SecretService). Never in YAML, never in logs, never in JSONL. |
| **No camera/audio ingestion** | `_EXCLUDED_DOMAINS` hard-coded: `camera`, `media_player`, `tts`, `stt`, `conversation`. Cannot be overridden via config. |
| **Dual-exclusion contract** | Two exclusion layers work together: (1) Code `_EXCLUDED_DOMAINS` frozenset is the **hard floor** — always excluded, not configurable, enforced regardless of YAML; (2) YAML `exclude_domains` in connectors.yaml is **additive** — user can exclude MORE domains beyond the floor, but can NEVER un-exclude the hard-coded set. If a user removes `camera` from YAML, the code frozenset still blocks it. |
| **PII guard on entity names** | All entity `friendly_name` values pass through `pii_guard.filter_text()` before writing to state files. Device IPs are never written — only entity_id names. |
| **Presence is consent-gated** | `device_tracker.*` entities processed ONLY if `integrations.homeassistant.presence_tracking: true`. Adults and minors have separate consent flags. State stores only `home`/`not_home`/`unknown` — never coordinates, zones, or GPS. |
| **Phase 2 autonomy floor** | All `ha_service_call` actions have `autonomy_floor: true`. No device control without explicit user approval per action. Lock/alarm actions require `min_trust: 2` + elevated friction. |
| **Audit trail** | All entity fetches logged to `state/audit.md`: `HA_FETCH | entities: N | offline: M | timestamp`. All service calls: `HA_SERVICE_CALL | service: X | entity_id: Y | approved_by: Z`. |
| **No IP addresses in state** | Device IPs from HA attributes are stripped before writing to state/home_iot.md. Only entity_id and friendly_name stored. |

### 5.2 Threat Model

| Threat | Likelihood | Impact | Mitigation |
|--------|-----------|--------|------------|
| **HA token exfiltration** | Low | High — full device control | Token in keyring (OS-protected). Never in logs or state files. Keyring access requires user session. |
| **HA token exposed in JSONL** | Low | High | Token passed via `auth_context` dict, NEVER included in JSONL output records. Filter assertion in health_check. |
| **Rogue entity names contain PII** | Medium | Medium | All entity names pass through PII guard before state write. HA entity names are typically user-set and could contain names ("Alice's Phone"). |
| **HA API returns unexpected data** | Medium | Low | Schema validation on response. Unknown entity domains filtered out. Max entity cap (500) prevents OOM. |
| **Man-in-the-middle on LAN** | Low | Medium | HA supports HTTPS. Recommend HTTPS in setup guide. Token transmitted via HTTP header. |
| **HA instance compromised** | Very Low | Critical | Artha treats HA as untrusted data source. No code execution from HA responses. Entity states are strings/numbers only. |
| **Presence tracking stalking risk** | Low (consent-gated) | Critical | Separate consent flags for adults and minors. `presence_minors: true` requires explicit acknowledgment during setup. State stores only home/away binary — never location data. |
| **Phase 2: unintended device actuation** | Medium (Phase 2 only) | High for locks/alarm | Service allowlist (whitelist, not blocklist). Autonomy floor on all actions. Lock/alarm require trust L2 + high friction. |

### 5.3 Nabu Casa / Cloud Relay Considerations

If the user has Nabu Casa configured, HA is accessible via a public URL
(`https://xxxxx.ui.nabu.casa`). This changes the security model:

| LAN-Only | Nabu Casa |
|----------|-----------|
| Token stays on local network | Token transmitted over internet (TLS) |
| No external dependency | Requires Nabu Casa cloud service availability |
| Unreachable off-LAN | Reachable from anywhere |

**If Nabu Casa detected during setup:**
1. Warn: "⚠️ Nabu Casa relay detected. HA token will be transmitted over the internet (TLS-encrypted). Artha's default is LAN-only."
2. Ask: "Continue with cloud relay? (yes/no)"
3. If yes: store `cloud_relay: true` in connectors.yaml. Log to audit.md.
4. If no: store LAN IP only. Connector works only on home network.

---

## §6 — Assumptions Registry

### Verified Assumptions

| ID | Assumption | Verification Method | Status |
|----|-----------|-------------------|--------|
| A-HA-01 | ConnectorHandler Protocol uses structural typing (no subclass required) | Read `scripts/connectors/base.py` — `@runtime_checkable` Protocol, duck typing | ✅ VERIFIED |
| A-HA-02 | pipeline.py `_HANDLER_MAP` is a string-keyed dict allowlist | Read `scripts/pipeline.py` lines 80-104 — `_HANDLER_MAP: dict[str, str]` | ✅ VERIFIED |
| A-HA-03 | skill_runner.py `_ALLOWED_SKILLS` is a frozenset allowlist | Read `scripts/skill_runner.py` lines 36-52 — `_ALLOWED_SKILLS: frozenset[str]` | ✅ VERIFIED |
| A-HA-04 | BaseSkill ABC requires `pull()`, `parse()`, `execute()`, `to_dict()`, `compare_fields` | Read `scripts/skills/base_skill.py` — all abstract methods confirmed | ✅ VERIFIED |
| A-HA-05 | `_SIGNAL_ROUTING` in action_composer.py accepts new entries (dict, not frozen) | Read `scripts/action_composer.py` — `_SIGNAL_ROUTING: dict[str, dict[str, Any]]` (mutable) | ✅ VERIFIED |
| A-HA-06 | Auth supports `api_key` method via keyring | Read `scripts/lib/auth.py` — `load_api_key(credential_key)` uses `keyring.get_password()` | ✅ VERIFIED |
| A-HA-07 | No IoT/HA code exists in current codebase | Searched for `home.assistant`, `homeassistant`, `iot`, `smart.home` — 0 code results, only spec references | ✅ VERIFIED |
| A-HA-08 | PRD F7.4 (P1) and F12.5 (P2) are the canonical HA requirements | Read `specs/artha-prd.md` lines 543, 684 — confirmed | ✅ VERIFIED |
| A-HA-09 | Tech Spec lists HA as "Phase 2, Local API, LAN Token, requests" | Read `specs/artha-tech-spec.md` line 476 — confirmed | ✅ VERIFIED |
| A-HA-10 | Platform gating precedent exists (`platform: macos_only` in skills.yaml) | Read `config/skills.yaml` line 83 — `whatsapp_last_contact: platform: macos_only` | ✅ VERIFIED |
| A-HA-11 | WorkIQ bridge set precedent for non-blocking connector failure | Read `config/Artha.md` Step 4 — "WorkIQ failures are always non-blocking" | ✅ VERIFIED |
| A-HA-12 | rss_feed.py is stdlib-only connector (no third-party deps) | Read full `scripts/connectors/rss_feed.py` — `urllib` + `xml.etree` only | ✅ VERIFIED |
| A-HA-13 | `connectors.yaml` supports `entity_allowlist` / `entity_blocklist` patterns | Not present in current schema. YAML is extensible and connector reads its own config dict, but this is a NEW pattern with no precedent in existing connectors | ⚠️ NEW PATTERN (YAML extensible, verified connector reads config dict) |
| A-HA-14 | All 1,243 existing tests pass before any IoT changes | `pytest tests/ --tb=no -q` → "1243 passed, 5 skipped, 20 xfailed" on 2026-03-20 | ✅ VERIFIED |
| A-HA-15 | `user_profile.schema.json` supports `household.type` (single/couple/family/multi_gen/roommates) | Read `config/user_profile.schema.json` — confirmed at lines 100-140 | ✅ VERIFIED |
| A-HA-16 | Actions support `autonomy_floor: true` for mandatory human approval | Read `config/actions.yaml` + `trust_enforcer.py` — confirmed for email_send, whatsapp_send | ✅ VERIFIED |

### Unverified Assumptions (require clarification or runtime test)

| ID | Assumption | Risk if Wrong | Validation Plan |
|----|-----------|---------------|-----------------|
| A-HA-17 | HA long-lived access token is already generated | Setup script won't complete until token is created in HA UI | ⚠️ PARTIAL: HA is running and accessible (Ring + lights visible). Token generation not explicitly confirmed — setup_ha_token.py will create/validate it. |
| A-HA-18 | HA integrations for Ring, Brother, Gecko are currently configured in HA | Connector would return empty/minimal entity list | ⚠️ PARTIAL: Ring ✅ CONFIRMED (battery + camera entities visible). Lights ✅ CONFIRMED. Brother printer ❓ UNKNOWN. Gecko spa ❓ UNKNOWN. Graceful degradation handles missing integrations. |
| A-HA-19 | HA REST API returns `state`, `attributes`, `last_changed` for all entities | Different HA versions may have schema variations | **Validation**: First connector run logs response schema to tmp/ |
| A-HA-20 | `requests` library is available in Artha venv | Connector implementation depends on `requests` for HTTP | ✅ VERIFIED: `requests 2.32.3` + `keyring` (macOS Keychain backend) confirmed in venv |
| A-HA-21 | Presence tracking via device_tracker is acceptable for adults in this household | Privacy-sensitive; user must opt in | **CQ-HA2**: Explicit consent question |
| A-HA-22 | Presence tracking for minors requires different consent bar | Children's presence data is more sensitive | **CQ-HA2**: Explicit consent question (separate for minors) |
| A-HA-23 | Phase 2 device control is a desired future capability | Affects whether to design action handler interfaces now | **CQ-HA3**: Ask user about Phase 2 appetite |
| A-HA-24 | Nabu Casa cloud relay is NOT configured (LAN-only) | If Nabu Casa active, security model changes significantly | **CQ-HA4**: Ask user about cloud relay status |
| A-HA-25 | Raspberry Pi at 192.168.1.123 has sufficient resources for additional API load | RPi 4 should handle ~10 requests/catch-up easily; RPi 3 might struggle | **Validation**: Check HA `/api/config` for version + hardware hints |
| A-HA-26 | Gecko in.touch 2 swim spa is visible as HA entities | Depends on whether Gecko HA integration is configured | ⚠️ UNCONFIRMED: User did not mention Gecko in HA. R-HA-07 (spa unavailable) remains active. Graceful degradation confirmed — skill returns no spa signals if entities absent. |
| A-HA-27 | Matter devices (4 paired) are represented as standard HA entities | Matter devices exposed through HA integration should appear in `/api/states` | **Validation**: First connector run will confirm |
| A-HA-28 | `pipeline.py` `_enabled_connectors()` can be extended with `requires_lan` check | Currently checks `enabled` and `source_filter` ONLY — no platform or network gating exists | ⚠️ RESOLVED: LAN gating moved to connector self-gating in `fetch()`. No pipeline change needed. See §3.1a. |

---

## §7 — Clarification Requests

### CQ-HA1: Current HA Configuration

**Question:** What integrations are currently configured in Home Assistant at 192.168.1.123:8123?

**Why this matters:** The connector will only return useful data for devices that have
HA integrations configured. Specifically:
- Is Ring integration configured? (security cameras + doorbell)
- Is Brother printer integration configured? (IPP-based auto-discovery likely)
- Is Gecko in.touch 2 integration configured? (may need custom component)
- Is there an energy dashboard configured? (for consumption tracking)
- Are Apple TV devices acting as Thread border routers in HA?

**Action items based on answer:**
- Missing integrations: document setup steps as prerequisites
- All configured: proceed directly to connector implementation
- Gecko not available: swim spa monitoring deferred; note in spec

**Additionally:** Has a long-lived access token already been generated in HA?
If not: create via HA UI → Profile → Security → Long-Lived Access Tokens.

**⚠️ PARTIALLY RESOLVED (2026-03-20):**
- Ring integration: ✅ CONFIRMED — battery level and camera entities visible in HA
- Smart lights: ✅ CONFIRMED — various lights connected
- Brother printer: ❓ UNKNOWN — not mentioned; skill will produce zero printer signals gracefully
- Gecko in.touch 2: ❓ UNKNOWN — not mentioned; swim spa monitoring unavailable until confirmed (R-HA-07 open)
- Energy dashboard: ❓ UNKNOWN — not mentioned; energy anomaly detection deferred until confirmed
- Long-lived token: ❓ NOT EXPLICITLY CONFIRMED — setup_ha_token.py will generate/validate at setup time
- **Unblocked for Wave 1:** Connector can proceed. Unknown integrations produce empty entity sets, not errors.

### CQ-HA2: Presence Tracking Consent — NON-BLOCKING (default: off)

**Question:** Is presence detection acceptable for this household?

**Context:** HA device_tracker entities can detect who's home based on network
presence, Bluetooth, or companion app location. Artha would store ONLY `home`/`not_home`/
`unknown` — never GPS coordinates, zones, or movement history.

**Sub-questions:**
- **Adults (Ved, Archana):** Is tracking home/away status acceptable? Used for compound
  signals like "Everyone's home — family dinner opportunity" and "Security check —
  travel mode but family member still tracked as home."
- **Minors (Parth, Trisha):** Is tracking acceptable? Higher privacy bar. Used for
  screen-time awareness compound signals and "child hasn't returned home" safety alerts.
- **Guests:** Never tracked (no guest presence entities ingested).

**Options:**
- **A: All adults + all minors** (full compound signal intelligence)
- **B: Adults only** (no minor tracking — screen-time compound signals disabled)
- **C: No presence tracking** (everyone's-home and security-aware travel disabled)
- **D: Defer decision** (implement presence consent UI but default off)

### CQ-HA3: Phase 2 Device Control Appetite — NON-BLOCKING (Wave 4 gate)

**Question:** Is Phase 2 (device control via HA service calls) a desired future capability?

**Context:** Phase 1 is strictly read-only. Phase 2 would allow actions like:
- "Turn off all lights" (switch.turn_off)
- "Set thermostat to 68°F" (climate.set_temperature)
- "Lock front door" (lock.lock — elevated trust + friction)
- "Arm security system to Away" (alarm.arm_away — elevated trust + friction)

**Impact on Phase 1 design:**
- If Phase 2 desired: design connector with service call capability from the start (dormant until enabled)
- If Phase 2 NOT desired: simpler connector; no service call code needed
- If Phase 2 uncertain: design interfaces but don't implement handlers

### CQ-HA4: Nabu Casa Cloud Relay Status — NON-BLOCKING (default: LAN-only)

**Question:** Is Nabu Casa (cloud remote access) configured for HA?

**Why this matters:**
- **If NO (LAN-only):** Simplest security model. HA data only available when on home network.
  Off-LAN catch-ups skip IoT data silently.
- **If YES:** Token traverses the internet (TLS-encrypted). Allows IoT data from anywhere
  (Cowork VM, travel, etc.) but increases attack surface. Requires explicit user acknowledgment.

### CQ-HA5: Nudge Daemon HA Access — NON-BLOCKING (Wave 3+ dependency)

**Question:** Should the proactive nudge daemon (Enhancement 4 from act-reloaded.md,
running every 5-15 minutes) be able to poll HA for critical alerts?

**Use case:** Security camera goes offline at 3 AM → nudge daemon detects → sends
Telegram alert within 15 minutes (instead of waiting for next morning catch-up).

**Trade-off:**
- **YES:** Real-time critical security alerting via Artha channel
- **NO:** Security alerts only surface at next catch-up (could be hours)

**Note:** HA itself has notification capabilities (HA Companion App, HA Automations).
This question is specifically about whether Artha should independently monitor
HA outside of catch-up sessions.

### CQ-HA6: Device Classification Confirmation

**Question:** Please confirm the device criticality classification:

| Classification | Devices | Alert Level |
|---------------|---------|-------------|
| **🔴 CRITICAL** | Ring (all: cameras, motion sensors, battery, connectivity) | Immediate alert |
| **🟠 MONITORED** | Smart lights, smart switches/plugs, HVAC, swim spa (Gecko — if integrated), Brother printer | Standard alert |
| **🟡 INFORMATIONAL** | Alexa devices, Fire TV, TVs, Nest Hub, Sonos, Apple TVs (note: `media_player.*` excluded from data fetch — offline state not tracked) | Low priority |
| **⬜ EXCLUDED** | Computers, phones (unless presence-tracked), private MACs, camera video streams (privacy) | Not monitored |

**✅ RESOLVED (2026-03-20):** User confirmed Ring=CRITICAL and lights=MONITORED (promoted from INFORMATIONAL in original proposal). Alexa devices and TVs confirmed INFORMATIONAL. Brother printer and Gecko spa not explicitly classified — defaulted to MONITORED based on operational relevance (toner/maintenance alerting is actionable). Reclassify in `_MONITORED_DEVICES` if desired.

**Key change from original proposal:** lights promoted from INFORMATIONAL → MONITORED. This adds `light.*` and `switch.*` to `_MONITORED_DEVICES`.

---

## §8 — Testing Strategy

### 8.1 Unit Tests: `tests/unit/test_homeassistant_connector.py`

```python
"""Test coverage for scripts/connectors/homeassistant.py"""

class TestFetch:
    """Test fetch() method conformance to ConnectorHandler Protocol."""
    
    def test_fetch_yields_valid_records(self, mock_ha_response):
        """fetch() with valid HA response yields records with required fields."""
        
    def test_fetch_filters_excluded_domains(self, mock_ha_response):
        """Camera, media_player, tts entities excluded from output."""
        
    def test_fetch_respects_entity_allowlist(self, mock_ha_response):
        """Only allowlisted entities returned when allowlist is non-empty."""
        
    def test_fetch_respects_entity_blocklist(self, mock_ha_response):
        """Blocklisted entities excluded even if in included domains."""
        
    def test_fetch_handles_ha_unreachable(self):
        """fetch() yields nothing and logs error when HA is unreachable."""
        
    def test_fetch_handles_auth_failure(self):
        """fetch() yields nothing when token is invalid (401 response)."""
        
    def test_fetch_respects_max_results_cap(self, mock_ha_large_response):
        """fetch() returns at most max_results entities."""
        
    def test_fetch_token_not_in_output(self, mock_ha_response):
        """Auth token NEVER appears in yielded record dicts."""
        
    def test_fetch_strips_ip_addresses(self, mock_ha_response_with_ips):
        """IP address attributes stripped from output records."""

class TestHealthCheck:
    """Test health_check() connectivity verification."""
    
    def test_health_check_success(self, mock_ha_api_ok):
        """health_check() returns True when /api/ returns 200."""
        
    def test_health_check_timeout(self):
        """health_check() returns False on timeout (does not raise)."""
        
    def test_health_check_auth_expired(self, mock_ha_401):
        """health_check() returns False on 401 Unauthorized."""

class TestLanDetection:
    """Test LAN reachability detection."""
    
    def test_private_ip_detected(self):
        """192.168.x.x recognized as LAN address."""
        
    def test_public_url_allowed(self):
        """Nabu Casa URL treated as reachable (delegates to health_check)."""
        
    def test_unreachable_ip_returns_false(self):
        """Connection refused on private IP returns False."""

class TestEntityFiltering:
    """Test domain filtering and PII scrubbing."""
    
    def test_camera_domain_excluded(self, entities_with_camera):
        """camera.* entities never appear in output."""
        
    def test_media_player_excluded(self, entities_with_media):
        """media_player.* entities never appear in output."""
        
    def test_pii_in_friendly_name_redacted(self, entities_with_pii_names):
        """Entity names containing PII patterns are filtered."""
```

### 8.2 Unit Tests: `tests/unit/test_home_device_monitor.py`

```python
"""Test coverage for scripts/skills/home_device_monitor.py"""

class TestDeviceOffline:
    """Test offline device detection threshold logic."""
    
    def test_device_offline_over_threshold(self):
        """Device unavailable >2h triggers offline alert."""
        
    def test_device_offline_under_threshold(self):
        """Device unavailable <2h does not trigger alert."""
        
    def test_critical_device_offline_is_red_alert(self):
        """Ring camera offline → 🔴 CRITICAL classification."""
        
    def test_monitored_device_offline_is_orange_alert(self):
        """Printer offline → 🟠 URGENT classification."""
        
    def test_informational_device_offline_is_yellow(self):
        """Apple TV offline → 🟡 STANDARD classification."""

class TestEnergyAnomaly:
    """Test energy spike detection."""
    
    def test_spike_above_threshold(self):
        """Energy >30% above average triggers anomaly."""
        
    def test_normal_variation_no_alert(self):
        """Energy within normal range produces no signal."""
        
    def test_no_history_no_comparison(self):
        """First run (no history) skips anomaly detection."""

class TestSupplyLevels:
    """Test consumable supply monitoring."""
    
    def test_toner_below_threshold(self):
        """Printer toner <20% triggers supply_low signal."""
        
    def test_toner_above_threshold(self):
        """Printer toner >20% produces no signal."""

class TestSpaMonitoring:
    """Test swim spa health checks."""
    
    def test_temp_deviation(self):
        """>5°F temp deviation from set point triggers alert."""
        
    def test_error_code_present(self):
        """Non-null error code triggers spa_maintenance signal."""
        
    def test_normal_operation(self):
        """Normal spa state produces no alert."""

class TestPresenceConsent:
    """Test consent-gated presence tracking."""
    
    def test_presence_disabled_skips_tracker(self):
        """device_tracker entities ignored when presence_tracking: false."""
        
    def test_presence_enabled_returns_binary(self):
        """With presence on, only home/not_home/unknown stored."""
        
    def test_gps_coordinates_never_stored(self):
        """Latitude/longitude attributes stripped even when presence enabled."""
        
    def test_minor_tracking_separate_consent(self):
        """Minor presence only processed when presence_minors: true."""

class TestCompareFields:
    """Test delta detection for cache comparison."""
    
    def test_compare_fields_defined(self):
        """compare_fields list is non-empty and contains expected keys."""
        
    def test_no_change_no_delta(self):
        """Same data twice produces no delta signal."""
        
    def test_new_offline_triggers_delta(self):
        """Device going offline detected as change."""

class TestSignalEmission:
    """Test DomainSignal creation."""
    
    def test_offline_produces_domain_signal(self):
        """Offline device produces DomainSignal with correct signal_type."""
        
    def test_signal_has_required_fields(self):
        """Every emitted signal has signal_type, domain, entity, urgency, impact."""
        
    def test_signal_contains_no_token(self):
        """Auth token never present in signal metadata."""
```

### 8.3 Integration Tests

| Test | What It Validates |
|------|-------------------|
| `test_pipeline_includes_ha_connector` | pipeline.py discovers and loads HA connector from connectors.yaml |
| `test_skill_runner_includes_ha_skill` | `home_device_monitor` in `_ALLOWED_SKILLS` |
| `test_ha_signal_routes_to_action` | DomainSignal("device_offline") → ActionComposer → ActionQueue |
| `test_ha_connector_graceful_failure` | HA unreachable → catch-up continues with 0 HA entities |
| `test_ha_data_deleted_at_step_18` | `tmp/ha_entities.json` removed in cleanup step |
| `test_preflight_ha_check_p1` | HA offline → P1 warning (non-blocking) |

### 8.4 Red Team / Security Tests

| Test | What We're Testing |
|------|-------------------|
| Token never in JSONL output | Scan all yielded records for auth token substring |
| Camera entities excluded | Force-include camera entity in mock → verify it's filtered out |
| IP addresses stripped | Mock entity with IP attributes → verify stripped in output |
| PII in entity names | Mock entity with "Alice's iPhone" → verify PII guard triggers |
| Presence requires consent | Set presence_tracking: false → verify device_tracker entities skipped |
| Phase 2 service allowlist | Attempt to call `homeassistant.restart` → verify blocked |
| Phase 2 autonomy floor | Service call with trust_level: 0 → verify rejection |
| Rate limit enforcement | >20 service calls/hour → verify rejection |

### 8.5 Performance Benchmarks

| Component | Target | Method |
|-----------|--------|--------|
| `homeassistant.fetch()` | <2s for 200 entities | Single GET /api/states, JSON parse |
| `homeassistant.health_check()` | <1s | Single GET /api/ |
| `home_device_monitor.parse()` | <50ms for 200 entities | In-memory threshold comparison |
| `home_device_monitor.execute()` | <3s total | pull + parse combined |
| State file update (home.md IoT section) | <100ms | YAML write for IoT section |

### 8.6 Test Data Fixtures

```python
@pytest.fixture
def mock_ha_response():
    """Minimal HA /api/states response with representative entity types."""
    return [
        {
            "entity_id": "binary_sensor.ring_floodlight_front",
            "state": "on",
            "attributes": {"friendly_name": "Ring Floodlight Front", "device_class": "connectivity"},
            "last_changed": "2026-03-19T10:30:00+00:00",
        },
        {
            "entity_id": "sensor.brother_toner_level",
            "state": "40",
            "attributes": {"friendly_name": "Brother Toner Level", "unit_of_measurement": "%"},
            "last_changed": "2026-03-19T14:00:00+00:00",
        },
        {
            "entity_id": "sensor.pse_energy_daily",
            "state": "38.5",
            "attributes": {"friendly_name": "Daily Energy", "unit_of_measurement": "kWh", "device_class": "energy"},
            "last_changed": "2026-03-20T06:00:00+00:00",
        },
        {
            "entity_id": "climate.swim_spa",
            "state": "heat",
            "attributes": {"friendly_name": "Swim Spa", "current_temperature": 102, "temperature": 104},
            "last_changed": "2026-03-20T08:00:00+00:00",
        },
        {
            "entity_id": "camera.ring_indoor",
            "state": "idle",
            "attributes": {"friendly_name": "Ring Indoor Camera"},
            "last_changed": "2026-03-20T08:00:00+00:00",
        },
    ]


@pytest.fixture
def mock_ha_response_schema_drift():
    """HA response with missing/extra attributes to verify graceful handling (M4).
    
    Tests schema drift between HA versions: missing expected fields,
    unknown extra fields, and type variations.
    """
    return [
        {
            # Missing 'attributes' entirely — older HA or sparse entity
            "entity_id": "sensor.unknown_device",
            "state": "42",
            "last_changed": "2026-03-20T08:00:00+00:00",
        },
        {
            # Extra unknown fields — newer HA version with additional data
            "entity_id": "sensor.future_sensor",
            "state": "on",
            "attributes": {"friendly_name": "Future Sensor", "new_field_2027": True},
            "last_changed": "2026-03-20T08:00:00+00:00",
            "context": {"id": "xxx", "parent_id": None},  # Extra field
            "last_reported": "2026-03-20T08:00:01+00:00",  # Extra field
        },
        {
            # State as non-string (some HA entities return numeric state)
            "entity_id": "sensor.numeric_state",
            "state": 23.5,
            "attributes": {"unit_of_measurement": "°C"},
            "last_changed": "2026-03-20T08:00:00+00:00",
        },
    ]
```

---

## §9 — Risk Registry

### Critical Risks

| ID | Risk | Wave | Likelihood | Impact | Mitigation | Residual Risk |
|----|------|------|-----------|--------|------------|---------------|
| R-HA-01 | HA token exfiltration via JSONL output or logs | 1 | Low | Critical | Token passed via auth_context dict only; assertion test that token never in yielded records; no token in stderr/stdout logging | Accepted (keyring + test coverage) |
| R-HA-02 | Phase 2 unintended device actuation (locks, alarm) | 4 | Medium | Critical | Service allowlist (not blocklist); autonomy_floor on ALL service calls; lock/alarm require trust L2 + high friction; 30-day read-only gate before Phase 2 | Accepted if CQ-HA3 → proceed |
| R-HA-03 | Presence tracking enables household surveillance | 2 | Low (consent-gated) | Critical | Separate opt-in for adults/minors; state stores ONLY home/away binary; no GPS/coordinates/zones; audit trail on all presence reads | Accepted if CQ-HA2 → Option A or B |

### High Risks

| ID | Risk | Wave | Likelihood | Impact | Mitigation |
|----|------|------|-----------|--------|------------|
| R-HA-04 | HA Raspberry Pi overwhelmed by Artha API calls | 1 | Low | High | Self-imposed rate: max 3 API calls per catch-up (states, config, optionally history). 10s timeout. RPi 4 handles this easily; RPi 3 may need monitoring. |
| R-HA-05 | Nabu Casa cloud relay exposure if misconfigured | 1 | Medium | High | Setup script detects public URL and warns. `cloud_relay: true` requires explicit acknowledgment. Default is LAN-only. |
| R-HA-06 | PII in HA entity names (e.g., "Alice's iPhone") | 1 | Medium | Medium | PII guard scans all friendly_name values before state write. Test fixture includes PII-containing entity names. |

### Moderate Risks

| ID | Risk | Wave | Likelihood | Impact | Mitigation |
|----|------|------|-----------|--------|------------|
| R-HA-07 | Gecko swim spa not available as HA entities | 2 | Medium | Low | Swim spa monitoring gracefully degraded — no error, just "spa: not available" in state. CQ-HA1 clarifies. |
| R-HA-08 | Energy anomaly false positives (seasonal variation) | 2 | High | Low | 7-day rolling average accounts for immediate weather. Compound signal with NOAA adds context. User can adjust ENERGY_SPIKE_PCT threshold. |
| R-HA-09 | Device offline false positives (WiFi drops, HA restart) | 2 | High | Low | 2-hour threshold (not 5-minute). HA restart is brief (~2 min). WiFi drops recover quickly. Critical devices get longer evaluation window if needed. |
| R-HA-10 | Cross-platform LAN detection unreliable | 1 | Medium | Medium | TCP connect probe (not ICMP ping). 2s timeout. Fallback: if probe fails but health_check succeeds, proceed. VPN edge case: corporate VPN may route 192.168.x.x to wrong LAN — resolved naturally by health_check token validation against actual HA instance. |

### Low Risks

| ID | Risk | Wave | Likelihood | Impact | Mitigation |
|----|------|------|-----------|--------|------------|
| R-HA-11 | HA API schema changes between versions | 1 | Low | Medium | Schema validation on response. Log unknown fields. HA REST API has been stable for years. |
| R-HA-12 | Matter device entities have non-standard attributes | 1 | Low | Low | Connector handles missing attributes gracefully. Matter entities go through HA's standard state API. |
| R-HA-13 | Test fixture staleness as HA ecosystem evolves | All | Medium | Low | Fixtures modeled on actual HA entity schemas with version in fixture comments. Include schema-drift fixtures with missing/extra attributes to verify graceful handling. |

---

## §10 — Implementation Waves & Timeline

### Wave Sequencing (Strangler Fig Pattern)

```
Wave 1: Read-Only Connector        ──── Foundation
  ├─ homeassistant.py (connector)
  ├─ connectors.yaml registration
  ├─ pipeline.py _HANDLER_MAP entry
  ├─ setup_ha_token.py
  ├─ preflight.py P1 check
  ├─ LAN detection logic
  └─ Unit tests (connector)

Wave 2: Deterministic Skill         ──── Intelligence
  ├─ home_device_monitor.py (skill)
  ├─ skills.yaml registration
  ├─ skill_runner.py _ALLOWED_SKILLS entry
  ├─ action_composer.py signal routing (5 entries)
  ├─ prompts/home.md IoT section
  ├─ state/home_iot.md companion file
  └─ Unit tests (skill + signals)

Wave 3: Cross-Domain Compound       ──── Cross-Domain
  ├─ Step 8f compound signal rules (5 additions)
  ├─ Briefing format additions
  ├─ Energy history tracking in state/home_iot.md
  └─ Integration tests

Wave 4: Device Control (Phase 2)    ──── Action Layer
  ├─ homeassistant_service.py (action handler)
  ├─ actions.yaml registration
  ├─ action_composer.py Phase 2 signals
  ├─ Security review gate
  └─ Red team tests
```

### Dependency Graph

```
Wave 1 (Connector)
  │
  ├── Wave 2 (Skill) ─── depends on Wave 1 (reads connector output)
  │     │
  │     └── Wave 3 (Compound Signals) ─── depends on Wave 2 (reads skill signals)
  │
  └── Wave 4 (Phase 2) ─── depends on Waves 1-3 production validation (30-day gate)
```

### Feature Flags

Each wave is feature-flagged in `config/artha_config.yaml`:

```yaml
integrations:
  homeassistant:
    connector_enabled: false      # Wave 1: enable after setup
    skill_enabled: false          # Wave 2: enable after Wave 1 validated
    compound_signals: false       # Wave 3: enable after Wave 2 validated
    phase2_control: false         # Wave 4: enable after 30-day gate + CQ-HA3
```

Disabling any flag reverts to pre-wave behavior with zero data loss.

**Rollback procedure (H4):** When disabling HA integration:
1. Set feature flags to `false` — connector and skill stop executing immediately.
2. `state/home_iot.md` retains `iot_devices` and `iot_energy` YAML sections with stale data.
   These are inert — the AI skips them when no fresh IoT data arrives.
3. If complete removal desired: delete `state/home_iot.md` entirely (machine-owned,
   safe to remove). No manual section surgery needed.
4. `tmp/ha_entities.json` is deleted at Step 18 as part of normal ephemeral cleanup.
5. Keyring token persists until manually removed: `python -c "import keyring; keyring.delete_password('artha', 'artha-ha-token')"`.

### Where This Fits in ACT Reloaded Waves

The IoT enhancement is orthogonal to the 10 enhancements in `specs/act-reloaded.md`.
It can be implemented in parallel with any ACT Reloaded wave.

**Recommended placement:** Between Wave 1 (signals) and Wave 2 (proactive) of ACT Reloaded.
Rationale: IoT connector provides new signal sources that benefit from the email signal
extractor (Enhancement 1) but don't depend on proactive nudging (Enhancement 4).

If nudge daemon (Enhancement 4) ships first, Wave 4's CQ-HA5 (nudge daemon HA polling)
becomes implementable immediately.

---

## §11 — Operational Constraints

### Backward Compatibility

- **Zero-downtime:** All IoT features are opt-in via config flags. Default: disabled.
  Existing users are completely unaffected.
- **State file compatibility:** IoT state lives in `state/home_iot.md` (new machine-owned
  companion file). `state/home.md` is NOT modified — zero risk to existing human-authored
  content. Net-negative write guard protects against data loss during update.
- **Config compatibility:** New `connectors.yaml` entry, new `skills.yaml` entry, new
  `actions.yaml` entry (Phase 2). All are additive blocks. Existing entries unchanged.
- **Pipeline compatibility:** New `_HANDLER_MAP` entry. Existing connectors unchanged.
- **Test compatibility:** All 1,243 existing tests must pass with IoT code present
  but disabled. IoT tests are additive.

### Observability

HA introduces a new boundary with LAN reachability, local auth, privacy filtering,
and optional device actuation. This requires structured observability beyond basic
metrics.

**Counters** (incremented per catch-up):
| Counter | Description |
|---------|-------------|
| `ha.fetch.success` | Successful `/api/states` fetches |
| `ha.fetch.failure` | Failed fetches (timeout, auth, network) |
| `ha.entities.accepted` | Entities passing domain + blocklist filters |
| `ha.entities.rejected` | Entities dropped by filters (excluded domains, blocklist) |
| `ha.privacy.pii_drops` | Entity names redacted by PII guard |
| `ha.privacy.ip_strips` | IP address attributes stripped |
| `ha.privacy.tracker_strips` | device_tracker attributes stripped to binary |
| `ha.signals.offline` | Offline device signals emitted |
| `ha.signals.energy` | Energy anomaly signals emitted |
| `ha.signals.supply` | Supply low signals emitted |
| `ha.signals.security` | Security device offline signals (critical) |
| `ha.service_call.approved` | Phase 2: approved service calls |
| `ha.service_call.executed` | Phase 2: executed service calls |
| `ha.service_call.rejected` | Phase 2: rejected service calls (trust, allowlist) |

**Timers** (milliseconds):
| Timer | Target | Description |
|-------|--------|-------------|
| `ha.fetch_ms` | <2000 | Time for GET `/api/states` + JSON parse |
| `ha.normalize_ms` | <500 | Time for domain filter + PII guard + sanitization |
| `ha.classify_ms` | <50 | Time for device classification (critical/monitored/info) |
| `ha.skill_total_ms` | <3000 | Total `home_device_monitor.execute()` time |
| `ha.health_check_ms` | <1000 | Time for GET `/api/` health probe |

**Trace spans** (if OpenTelemetry tracing enabled in `artha_config.yaml`):
| Span | Parent | Description |
|------|--------|-------------|
| `ha.fetch` | `pipeline.run` | Full connector fetch cycle |
| `ha.normalize` | `ha.fetch` | Entity filtering + PII sanitization |
| `ha.write_artifact` | `ha.fetch` | Temp file write (`tmp/ha_entities.json`) |
| `ha.classify` | `ha.skill` | Device classification pass |
| `ha.emit_signal` | `ha.skill` | DomainSignal construction |
| `ha.service_call` | `action.execute` | Phase 2: HA service call execution |

**Health-check surface:**

```yaml
# Written to health-check.md after each catch-up with HA enabled
ha_metrics:
  last_fetch: ISO-8601
  entities_fetched: N
  entities_filtered: N   # Excluded domains
  entities_offline: N
  critical_offline: N
  energy_kwh_today: N
  spa_temp_f: N
  signals_emitted: N
  fetch_time_ms: N
  normalize_time_ms: N
  skill_time_ms: N
  health_check_passed: true|false
  cloud_relay: false
  pii_drops: N
  privacy_strips: N
```

All counters and timers are collected in-memory during the catch-up run and
written to `health-check.md` at Step 16. If OpenTelemetry tracing is enabled,
spans are exported to the configured OTLP endpoint. No additional dependencies
are introduced — counters are plain Python dicts; spans use the existing
`artha_config.yaml → tracing` configuration.

### Security-by-Design Checklist

| Control | Applied To | Enforcement |
|---------|-----------|-------------|
| Token in keyring | HA long-lived access token | `setup_ha_token.py` + `lib/auth.py` |
| Token assertion test | All JSONL output + logs | `test_fetch_token_not_in_output()` |
| PII guard on entity names | `friendly_name` attributes | `pii_guard.filter_text()` before state write |
| Domain exclusion (hard-coded) | camera, media_player, tts, stt, conversation | `_EXCLUDED_DOMAINS` frozenset; not configurable |
| IP address stripping | Device attributes | Regex strip before state write |
| Presence consent gate | device_tracker entities | `user_profile.yaml → presence_tracking` flag |
| Autonomy floor (Phase 2) | All ha_service_call actions | `autonomy_floor: true` in actions.yaml |
| Service allowlist (Phase 2) | _SERVICE_ALLOWLIST frozenset | Whitelist; not configurable to prevent bypass |
| Rate limiting (Phase 2) | Service calls | 20/hour, 100/day via action_rate_limiter |
| Audit trail | All HA operations | `state/audit.md` entries for fetch + service calls |

---

## §12 — Success Metrics

### Per-Wave KPIs

| Wave | KPI | Target | Measurement |
|------|-----|--------|-------------|
| 1 (Connector) | HA entity fetch success rate | ≥95% of on-LAN catch-ups | `ha_metrics.health_check_passed` in health-check.md |
| 1 (Connector) | Fetch latency | <2s for 200 entities | `ha_metrics.fetch_time_ms` |
| 2 (Skill) | Actionable signals per catch-up | ≥1 signal on average | `ha_metrics.signals_emitted` rolling average |
| 2 (Skill) | False positive rate (offline alerts) | <10% over 30 catches | User calibration: dismiss/adjust threshold |
| 3 (Compound) | Cross-domain insights per week | ≥1 compound signal | Step 8f compound signal count |
| 4 (Phase 2) | Service call success rate | ≥99% (target the happy path) | Action audit: succeeded/failed ratio |

### System-Level Impact

| Metric | Before IoT | After IoT (target) |
|--------|------------|---------------------|
| Domains with real-time data | 5 (email, calendar, work, social, kids) | 6 (+home IoT) |
| Signals per catch-up | ~5 (skills only) | ~8 (+HA signals) |
| Cross-domain compound signals | ~3/week | ~5/week (+IoT compounds) |
| Home domain alert coverage | 0% (no device visibility) | ≥80% (critical devices monitored) |
| Mean time to detect Ring offline | ∞ (manual check only) | <2 hours (threshold-based) |

---

## §13 — Appendix: File Inventory

### New Files

| File | Wave | Type | Lines (est) |
|------|------|------|-------------|
| `scripts/connectors/homeassistant.py` | 1 | Production | 250 |
| `scripts/setup_ha_token.py` | 1 | Setup | 120 |
| `scripts/skills/home_device_monitor.py` | 2 | Production | 300 |
| `scripts/actions/homeassistant_service.py` | 4 | Production | 200 |
| `state/home_iot.md` | 2 | State | 30 |
| `tests/unit/test_homeassistant_connector.py` | 1 | Test | 250 |
| `tests/unit/test_home_device_monitor.py` | 2 | Test | 300 |
| `tests/unit/test_ha_service_handler.py` | 4 | Test | 200 |
| `tests/integration/test_ha_pipeline.py` | 3 | Test | 150 |
| **Total New Files** | | | **9 files** |
| **Total New Production LOC** | | | **~900** |
| **Total New Test LOC** | | | **~900** |

### Modified Files

| File | Wave | Change Type | Scope |
|------|------|-------------|-------|
| `config/connectors.yaml` | 1 | Add `homeassistant` connector block | ~35 LOC additive |
| `scripts/pipeline.py` | 1 | Add `_HANDLER_MAP` entry + `requires_lan` check | ~15 LOC additive |
| `scripts/preflight.py` | 1 | Add P1 HA connectivity check | ~25 LOC additive |
| `config/skills.yaml` | 2 | Add `home_device_monitor` entry | ~10 LOC additive |
| `scripts/skill_runner.py` | 2 | Add to `_ALLOWED_SKILLS` | ~1 LOC additive |
| `scripts/action_composer.py` | 2+4 | Add 5+2 signal routing entries | ~40 LOC additive |
| `prompts/home.md` | 2 | Add IoT section | ~60 LOC additive |
| `state/home_iot.md` | 2 | New machine-owned IoT state companion file | ~30 LOC additive |
| `config/actions.yaml` | 4 | Add `ha_service_call` entry | ~20 LOC additive |
| `config/implementation_status.yaml` | All | Register IoT features | ~25 LOC additive |
| `config/artha_config.yaml` | All | Feature flags | ~10 LOC additive |
| `config/user_profile.schema.json` | 1 | Add `integrations.homeassistant` schema | ~20 LOC additive |
| `config/domain_registry.yaml` | 2 | Update `home` domain routing_keywords | ~5 LOC additive |

---

## §14 — Appendix: Implementation Status Registration

Add to `config/implementation_status.yaml` as each wave ships:

```yaml
  # ── IoT / Home Assistant (specs/iot.md) ────────────────────────────────
  ha_connector:
    spec: "specs/iot.md §3.1-3.6"
    status: not_started      # → implemented after Wave 1
    confidence: low
    note: "Wave 1: connector + setup + preflight + LAN detection"
  ha_device_monitor_skill:
    spec: "specs/iot.md §3.7-3.12"
    status: not_started      # → implemented after Wave 2
    confidence: low
    note: "Wave 2: skill + signals + state file additions"
  ha_compound_signals:
    spec: "specs/iot.md §3.13-3.14"
    status: not_started      # → implemented after Wave 3
    confidence: low
    note: "Wave 3: cross-domain compound signal rules"
  ha_device_control:
    spec: "specs/iot.md §3.15-3.17"
    status: not_started      # → implemented after Wave 4 (Phase 2)
    confidence: low
    note: "Wave 4 (Phase 2): service call handler. Gated on 30-day read-only + CQ-HA3."
```

---

## §15 — Appendix: Quick Start (Post-Approval)

After clarification questions (CQ-HA1 through CQ-HA6) are answered:

### Step 1: Verify Prerequisites
```bash
# Verify HA is reachable
curl -s http://192.168.1.123:8123/api/ -H "Authorization: Bearer $HA_TOKEN"
# Expected: {"message": "API running."}

# Verify requests library available
python3 -c "import requests; print(requests.__version__)"

# Verify entity count
curl -s http://192.168.1.123:8123/api/states -H "Authorization: Bearer $HA_TOKEN" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"
```

### Step 2: Run Setup
```bash
python3 scripts/setup_ha_token.py
# Interactive: URL, token, validation, keyring store, config update
```

### Step 3: Test Connector
```bash
python3 scripts/pipeline.py --source homeassistant --verbose
# Should output JSONL entity records
```

### Step 4: Enable Skill
```bash
# Edit config/skills.yaml → home_device_monitor → enabled: true
# Run catch-up to verify skill execution
```

### Step 5: Validate
```bash
python3 -m pytest tests/unit/test_homeassistant_connector.py tests/unit/test_home_device_monitor.py -v
# All new tests pass + existing 1,243 tests still pass
python3 -m pytest tests/ --tb=no -q
```

---

---

## §16 — Appendix: Review Incorporation Log (v1.0.0 → v1.1.0)

Architectural review findings incorporated into this revision:

### Critical (C1-C6) — All Resolved

| ID | Finding | Resolution | Section |
|----|---------|------------|---------|
| C1 | `compare_fields` must be `@property`, not class attribute | Changed to `@property` matching all existing skills | §3.7 |
| C2 | Connector → Skill data handoff undefined (pipeline streams to stdout, not files) | Added §3.1a: connector writes `tmp/ha_entities.json` as side effect; skill reads it | §3.1a |
| C3 | Missing `get_skill()` factory function required by `skill_runner.py` | Added factory function to skill module spec | §3.7 |
| C4 | `_enabled_connectors()` has no platform gating — claim was incorrect | LAN gating moved to self-gating inside connector `fetch()`, matching WorkIQ pattern | §3.1, §3.6 |
| C5 | Skills don't emit `DomainSignal` — orchestrator does | Added §3.7a: skill constructs signals directly (new pattern, documented) | §3.7a |
| C6 | Wave 3 compound signals are LLM-driven, contradicts principle #2 | Updated principle #2 to distinguish deterministic (W1-2) vs heuristic (W3) | §0, §3.13 |

### High (H1-H5) — All Resolved

| ID | Finding | Resolution | Section |
|----|---------|------------|---------|
| H1 | Energy anomaly cold-start underspecified | Added cold-start protocol: 7-day minimum, informational-only during warmup | §3.7 `_check_energy_anomaly` |
| H2 | Dual exclusion lists need precedence contract | Clarified: code frozenset = hard floor, YAML = additive | §3.2, §5.1 |
| H3 | `del token` doesn't guarantee memory erasure | Updated to overwrite-before-del with honest limitation note | §3.4 |
| H4 | No rollback procedure for state mutations | Added rollback procedure with manual cleanup steps | §10 |
| H5 | `_ALLOWED_MODULES` auto-derives from `_HANDLER_MAP` | Documented auto-derivation to prevent confusion | §3.3 |

### Moderate (M1-M4) — All Resolved

| ID | Finding | Resolution | Section |
|----|---------|------------|---------|
| M1 | VPN edge case for LAN detection | Documented: VPN resolves via health_check token validation | §3.1 |
| M2 | A-HA-13 verification status misleading | Changed to ⚠️ NEW PATTERN status | §6 |
| M3 | Skill `execute()` return schema missing | Added full return schema in `execute()` and `parse()` docstrings | §3.7 |
| M4 | Test fixtures need schema drift coverage | Added `mock_ha_response_schema_drift` fixture | §8.6 |

---

## §17 — Appendix: External Review Incorporation Log (v1.1.0 → v1.2.0)

External review findings incorporated into this revision. 8 findings triaged;
7 incorporated; 1 already resolved.

### Incorporated (7 findings)

| # | Finding | Severity | Resolution | Section |
|---|---------|----------|------------|---------|
| E1 | Connector→tmp→skill flow is a one-off architectural exception; no generic seam exists | High | Added ADR with three options evaluated (skill-owned fetch, self-contained coupling, generic artifact registry). Chose Option B with explicit escalation trigger for Option C if a second connector-backed skill appears. | §0 ADR |
| E3 | Raw HA dump to `tmp/` violates privacy model — `friendly_name` PII, device_tracker locations, and household topology touch disk before redaction | High | Added privacy contract: sanitize BEFORE write (PII guard, IP strip, tracker strip, domain exclusion all applied in `fetch()` before `tmp/ha_entities.json` is written). Added OneDrive sync note and `.nosync` recommendation. | §3.1a |
| E4 | Configuration ownership split against source-of-truth model — meaningful config spread across 3 files with no ownership contract | High | Added configuration ownership contract table: user_profile.yaml owns consent/identity/preferences, connectors.yaml owns handler wiring, artha_config.yaml owns kill switches only. Added `cloud_relay` and `critical_device_overrides` to user_profile. | §4.1 |
| E5 | Wave 3 too prompt-centric — safety-critical compound signals (travel + security offline) should not depend on model variance | Medium | Added deterministic correlator requirement: safety-critical compounds implemented as code (DomainSignal emission), not prompt rules. Non-safety compounds remain LLM-driven (acceptable for informational signals). | §3.13 (Wave 3 note) |
| E6 | State update plan brittle — machine-generated YAML sections appended to human-authored `state/home.md` risks write conflicts | Medium | Changed to `state/home_iot.md` — dedicated machine-owned companion file. AI reads both files during home domain processing. Rollback simplified (delete file vs. surgery on shared file). | §3.12, §10, §11, §13 |
| E7 | Phase 2 handler uses class pattern but actual handlers are module-level functions | Medium | Changed `class HAServiceCallHandler` to module-level functions (`validate`, `dry_run`, `execute`, `health_check`, `build_reverse_proposal`) matching `email_send.py` and all other existing handlers. | §3.15 |
| E8 | Observability underdesigned — no structured counters, timers, or trace spans | Medium | Added full observability contract: 14 counters, 5 timers, 6 trace spans (OTLP-compatible), expanded health-check surface with `normalize_time_ms`, `skill_time_ms`, `pii_drops`, `privacy_strips`. | §11 |

### Already Resolved (1 finding)

| # | Finding | Status |
|---|---------|--------|
| E2 | `requires_lan` not additive — pipeline has no constraint evaluation | Already resolved in v1.1.0: §3.1 self-gates inside `fetch()`, §3.6 confirms `requires_lan` is documentation-only metadata, matches WorkIQ pattern. |

### Not Adopted (with rationale)

| Suggestion | Rationale for deferral |
|------------|----------------------|
| Wave 0 framework seam (generic connector artifact registry) | Premature — only one connector-backed skill exists. ADR documents escalation trigger. YAGNI. |
| Full wave restructuring (0→1→2→3→4) | Current wave structure (1→2→3→4) is sound. ADR addresses the framework seam concern without restructuring. |
| Skill-owned fetch (bypass connector entirely) | Loses pipeline observability. HA data wouldn’t appear in JSONL stream. Connector pattern provides consistent metrics and health-check surface. |: Third-Pass Review Incorpora

---

## §18 — Appendix: Third-Pass Review Incorporation Log (v1.2.0 → v1.3.0)

Third-pass architectural review findings incorporated into this revision.
12 findings identified (4 Critical, 4 High, 4 Moderate); all 12 incorporated.

### Critical (C1-C4) — All Resolved

| ID | Finding | Resolution | Section |
|----|---------|------------|---------|
| C1 | `DomainSignal` (frozen dataclass) is NOT JSON-serializable; `skill_runner.py` caches to JSON — no serialization/deserialization contract defined | Added §3.7b Signal Serialization Contract: `dataclasses.asdict()` for serialization in `execute()`, `DomainSignal(**dict)` for deserialization in `skill_runner.py`. Round-trip fidelity guaranteed. | §3.7b |
| C2 | §2.4 Step 4 labeled twice (connector AND skill both "Step 4") — race condition ambiguity | Split into Step 4a (connector) → Step 4b (skill). Added ordering constraint note and sentinel pattern requirement. | §2.4 |
| C3 | `state/home_iot.md` write protocol has three gaps: non-atomic write, net-negative guard conflicts, unbounded `history[]` | Added atomic write protocol, net-negative guard exemption, and `history[-30:]` truncation. | §3.11, §3.12 |
| C4 | Step 12.5 "Orchestrator reads skill cache → ActionComposer" but orchestrator is the LLM — cannot call Python directly | Added §3.7c: `_route_deterministic_signals()` in `skill_runner.py` as Python code path. No AI in deterministic signal critical path. | §3.7c, §2.4 |

### High (H1-H4) — All Resolved

| ID | Finding | Resolution | Section |
|----|---------|------------|---------|
| H1 | `_ENTITY_CACHE.write_text()` is non-atomic — crash mid-write leaves corrupted file | Replaced with write-to-tmp + `os.replace()`. Added `json.JSONDecodeError` catch in `pull()`. | §3.1a |
| H2 | Wave 3 safety-critical correlator has no implementation home | Added `scripts/skills/security_travel_correlator.py` skeleton (~60 LOC), `_ALLOWED_SKILLS` registration, `skills.yaml` entry. | Wave 3 |
| H3 | `security_travel_conflict` signal type not registered in `_SIGNAL_ROUTING` | Added entry: `friction: high`, `min_trust: 0` (always notify — safety-critical). | §3.10 |
| H4 | `ha_url: ""` in connectors.yaml contradicts §4.1 config ownership contract | **SUPERSEDED by G2 (v1.4.0):** ha_url restored to `connectors.yaml` per §3.1b G2 resolution. URL is deployment topology, not user identity. §4.1 updated to match. | §3.1b, §3.2 |

### Moderate (M1-M4) — All Resolved

| ID | Finding | Resolution | Section |
|----|---------|------------|---------|
| M1 | `_check_device_offline` processes `device_tracker` with no cross-reference to §3.1a privacy contract | Added explicit note: pull() receives only `state` string — attributes stripped by connector. | §3.7 |
| M2 | `requires_connector: homeassistant` in skills.yaml — field not enforced by `skill_runner.py` | Added documentation-only note. Validation is in skill code (`pull()` returns empty list). | §3.8 |
| M3 | `.nosync` advisory too weak for privacy-sensitive temp file | Strengthened to mandatory: setup script MUST create `tmp/.nosync`. | §3.1a |
| M4 | No mechanism to enforce skill execution order | Added `execution_after: pipeline` to skills.yaml. Advisory today; noted for future parallel support. | §3.8 |

---

*ARTHA-IOT v1.4.0 — specs/iot.md — 2026-03-20*
