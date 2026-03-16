# Artha Connectors Guide

Artha uses a declarative connector architecture to ingest data from external
sources.  Each connector is defined in `config/connectors.yaml` and implemented
as a Python handler module in `scripts/connectors/`.

---

## Built-in Connectors

| Name | Type | Provider | Status |
|------|------|----------|--------|
| `gmail` | email | Google Gmail API | ✅ Active |
| `outlook_email` | email | Microsoft Graph | ✅ Active |
| `icloud_email` | email | iCloud IMAP | ✅ Active |
| `google_calendar` | calendar | Google Calendar API | ✅ Active |
| `outlook_calendar` | calendar | Microsoft Graph | ✅ Active |
| `icloud_calendar` | calendar | iCloud CalDAV | ✅ Active |
| `canvas_lms` | education | Canvas LMS API | ✅ Active |
| `onenote` | notes | Microsoft Graph OneNote | ✅ Active |
| `apple_health` | health | Local Apple Health export | ⭕ Opt-in (disabled by default) |

---

## Running the Pipeline

```bash
# Fetch all sources (last 48 hours)
python scripts/pipeline.py --verbose

# Fetch since a specific date
python scripts/pipeline.py --since "2026-03-10T00:00:00Z"

# Fetch one source only
python scripts/pipeline.py --source gmail --verbose

# Health check all connectors
python scripts/pipeline.py --health

# List all configured connectors
python scripts/pipeline.py --list
```

---

## Adding a New Connector

### Step 1: Write the handler module

Create `scripts/connectors/<your_source>.py` implementing two functions:

```python
from typing import Any, Dict, Iterator

def fetch(
    *,
    since: str,           # ISO-8601 start timestamp
    max_results: int,
    auth_context: Dict[str, Any],
    source_tag: str,
    **kwargs: Any,
) -> Iterator[Dict[str, Any]]:
    """Yield records from your source."""
    ...

def health_check(auth_context: Dict[str, Any]) -> bool:
    """Return True if credentials are valid and source is reachable."""
    ...
```

The `fetch()` function **must** be a generator (use `yield`).  Each yielded dict
should include a `"source"` key (set to `source_tag`) for provenance tracking.

### Step 2: Register in connectors.yaml

Add a block to `config/connectors.yaml`:

```yaml
connectors:
  - name: my_source
    type: <email|calendar|notes|education|custom>
    provider: <provider name>
    enabled: true
    auth:
      method: <api_key|oauth_google|oauth_msgraph|icloud_app_password>
      credential_key: "artha-my-source-key"   # keyring service name
    fetch:
      handler: "connectors.my_source"
      max_results: 200
    retry:
      max_attempts: 3
      base_delay_seconds: 1.0
```

### Step 3: Store credentials

```bash
python -c "
import keyring
keyring.set_password('artha-my-source-key', 'artha', 'your-api-key-here')
"
```

### Step 4: Verify

```bash
python scripts/pipeline.py --source my_source --health
python scripts/pipeline.py --source my_source --verbose
```

That's it.  No changes to the core instruction file.  The pipeline picks up new
connectors automatically.

---

## Auth Methods

| Method | `auth.method` | What it loads |
|--------|--------------|---------------|
| API key | `api_key` | `keyring.get_password(credential_key, "artha")` |
| Google OAuth | `oauth_google` | Refreshes token from `~/.tokens/gmail_token.json` |
| MS Graph OAuth | `oauth_msgraph` | Refreshes token from MSAL cache |
| iCloud App Password | `icloud_app_password` | Apple ID + app password from keyring |

Auth loading is handled by `scripts/lib/auth.py`.  The connector handler
receives a pre-populated `auth_context` dict — it never handles raw credentials.

---

## Output Schema

All connectors emit JSONL records with at minimum:

```json
{
  "source": "gmail",
  "id": "...",
  ...
}
```

Email connectors use a shared schema:

```json
{
  "source": "gmail",
  "id": "...",
  "thread_id": "...",
  "subject": "...",
  "from": "...",
  "to": "...",
  "date_iso": "2026-03-13T08:00:00Z",
  "body": "...",
  "labels": []
}
```

Calendar connectors use:

```json
{
  "source": "google_calendar",
  "id": "...",
  "summary": "...",
  "start": "2026-03-13T10:00:00Z",
  "end": "2026-03-13T11:00:00Z",
  "all_day": false,
  "location": "...",
  "attendees": []
}
```

---

## Community Extensions

The `config/connectors.yaml` file includes commented examples for community
connectors (Fastmail, ProtonMail Bridge).  To share a connector:

1. Write the handler module following the interface above.
2. Add a commented YAML block to `config/connectors.yaml`.
3. Submit a pull request with both files.

See [docs/contributing.md](contributing.md) for the contribution workflow.

---

## Apple Health Connector

The `apple_health` connector parses Apple Health exports **locally** — no network traffic, no Apple account needed.

### Enable

1. In `config/connectors.yaml`, set `apple_health.enabled: true`
2. Export your Apple Health data from the iPhone Health app:
   - Open **Health** → tap your profile photo → **Export All Health Data**
   - Transfer the `export.zip` to your Artha directory (e.g., `state/apple_health_export.zip`)
3. Run a catch-up: `catch me up` — the connector picks up the ZIP automatically

### Supported Metrics (16 HKQuantityTypeIdentifier types)

Steps, resting heart rate, walking heart rate, heart rate, body mass (weight),
BMI, body fat percentage, sleep analysis, blood pressure (systolic/diastolic),
blood oxygen, respiratory rate, active energy, VO2 max, environmental noise,
headphone audio exposure.

### Options (`config/connectors.yaml`)

```yaml
apple_health:
  enabled: true                # opt-in
  since: "365d"                # relative (NNd) or absolute ISO date
  default_max_results: 10000   # max records returned per metric type
```
