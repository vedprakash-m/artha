# Channel Bridge — Setup & Usage Guide

> **Artha Channel Bridge (ACB) v2.0.0**  
> Deliver your daily catch-up briefing to Telegram, Discord, or Slack — and query live state summaries interactively from your phone.

---

## Overview

The channel bridge adds two output layers to Artha:

| Layer | What it does | Script |
|-------|-------------|--------|
| **Layer 1** — Push | Sends a flash briefing after each catch-up (Step 20) | `scripts/channel_push.py` |
| **Layer 2** — Listen | Runs as a background daemon, responds to `/commands` | `scripts/channel_listener.py` |

Both layers are **opt-in** and **zero-dependency** for Telegram (stdlib `urllib.request` only). No existing catch-up pipeline is modified; `channel_push.py` is silently skipped if `config/channels.yaml` is absent.

---

## Quick Start (Telegram)

### 1. Create a bot

1. Open Telegram → search **@BotFather**
2. `/newbot` → follow prompts → copy the token

### 2. Store the token

```bash
python -c "import keyring; keyring.set_password('artha', 'artha-telegram-bot-token', 'YOUR_TOKEN_HERE')"
```

### 3. Run the setup wizard

```bash
python scripts/setup_channel.py --channel telegram
```

The wizard will:
- Detect your chat ID automatically (send one message to the bot first)
- Write `config/channels.yaml`
- Send a test message

### 4. Test it

```bash
python scripts/channel_push.py --dry-run   # Preview without sending
python scripts/channel_push.py             # Send now
```

### 5. (Optional) Start the interactive listener

```bash
python scripts/channel_listener.py --channel telegram --dry-run   # Validate setup
python scripts/channel_listener.py --channel telegram             # Start daemon
```

Then send `/help` to your bot.

---

## config/channels.yaml Reference

Copy from `config/channels.example.yaml` to `config/channels.yaml` (gitignored).

```yaml
defaults:
  push_enabled: true          # Enable post-catch-up push (Step 20)
  redact_pii: true            # Always true in production
  max_push_length: 500        # Max chars in push message
  listener_host: ""           # Hostname that runs the listener daemon
                              # (empty = any host; for multi-machine OneDrive safety)

channels:
  telegram:
    enabled: true
    adapter: "scripts/channels/telegram.py"
    auth:
      credential_key: "artha-telegram-bot-token"  # Keyring key
    recipients:
      primary:
        id: "123456789"          # Your Telegram chat ID
        access_scope: "full"     # full | family | standard
      family:
        id: "987654321"          # Optional family group chat ID
        access_scope: "family"   # Excludes: immigration, finance, estate
    features:
      push: true                 # Receive Layer 1 flash briefings
      interactive: true          # Enables Layer 2 /commands
    health_check:
      interval_minutes: 60
    retry:
      max_attempts: 3
      base_delay: 1
      max_delay: 30
```

### Access Scopes

| Scope | What you see |
|-------|-------------|
| `full` | All domains — same as CLI catch-up |
| `family` | All except: immigration, finance, estate, insurance, digital, boundary |
| `standard` | Calendar and task items only |

Access scope is per-recipient. A family group chat typically uses `family`; your personal chat uses `full`.

---

## Interactive Commands

Once the Layer 2 listener is running, send these to your bot:

| Command | Description |
|---------|-------------|
| `/status` | System health + active alerts |
| `/alerts` | All active alerts by severity |
| `/tasks` | Open action items (top 10) |
| `/quick` | Tasks ≤5 minutes (phone-ready) |
| `/domain <name>` | Deep-read a specific domain |
| `/unlock <PIN>` | 15-minute session for sensitive queries |
| `/help` | Command reference |

**Available domains:** `health`, `goals`, `calendar`, `tasks`, `comms`, `home`, `kids`, `dashboard`

### Access control

- **Sender whitelist**: Only `recipients` configured in `channels.yaml` can send commands. Unknown senders are silently ignored.
- **Rate limiting**: 10 commands/minute per sender; 60-second cooldown on breach.
- **Session tokens**: `/unlock <PIN>` grants a 15-minute session for sensitive domain queries. Set your PIN in keyring:
  ```bash
  python -c "import keyring; keyring.set_password('artha', 'artha-channel-pin', 'YOUR_PIN')"
  ```
- **No vault access**: Encrypted files (`.md.age`) are never read. All state comes from plaintext `state/*.md` files.

---

## Background Listener Service

### macOS (LaunchAgent)

```bash
python scripts/setup_channel.py --install-service
# Then:
launchctl load ~/Library/LaunchAgents/com.artha.channel-listener.plist
launchctl start com.artha.channel-listener
```

Logs: `~/Library/Logs/artha-channel-listener.log`

### Linux (systemd user service)

```bash
python scripts/setup_channel.py --install-service
# Then:
systemctl --user daemon-reload
systemctl --user enable artha-channel-listener
systemctl --user start artha-channel-listener
systemctl --user status artha-channel-listener
```

Logs: `journalctl --user -u artha-channel-listener -f`

### Windows (Task Scheduler)

```bash
python scripts/setup_channel.py --install-service
# Generates a configured XML in tmp/artha-listener.xml
# Register with NSSM or Task Scheduler (see instructions printed by the wizard)
```

---

## Multi-Machine Safety (OneDrive setups)

If you sync your Artha workspace across multiple machines with OneDrive, only **one machine** should run the interactive listener (to avoid competing `getUpdates` offsets):

```bash
python scripts/setup_channel.py --set-listener-host
```

This sets `defaults.listener_host` in `channels.yaml`. The listener on non-designated machines will log a notice and exit 0.

Layer 1 push deduplication is handled separately via `state/.channel_push_marker_YYYY-MM-DD.json` (12-hour window).

---

## Health Checks

```bash
python scripts/setup_channel.py --health          # Adapter health via setup wizard
python scripts/channel_listener.py --health       # Adapter health via listener
python scripts/channel_push.py --health           # Push hook health
python scripts/preflight.py                       # Includes channel health (P1 check)
```

All health check results are logged to `state/audit.md` as `CHANNEL_HEALTH` events.

---

## Audit Events

All channel activity is appended to `state/audit.md`. See `config/observability.md` for the full event table.

Key events:

- `CHANNEL_PUSH` — flash briefing sent after catch-up
- `CHANNEL_IN` — inbound command received (sender alias, not chat ID)
- `CHANNEL_OUT` — response sent (char count, whether PII was filtered)
- `CHANNEL_REJECT` — unknown sender silently rejected
- `CHANNEL_RATE_LIMIT` — sender exceeded rate limit

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| No push after catch-up | `defaults.push_enabled: true` in channels.yaml? |
| Bot not responding | `features.interactive: true`? Listener running? |
| "Listener host mismatch" | Expected — non-designated host exits cleanly |
| "health_check() failed" | Bot token valid? Bot not blocked? Network OK? |
| PII appears in messages | `redact_pii: true` in channels.yaml defaults? |
| Commands rejected | Sender chat ID matches `recipients[name].id` exactly? |

For extended troubleshooting see `docs/troubleshooting.md`.

---

## Security Checklist

- [ ] `config/channels.yaml` is in `.gitignore` ✓ (pre-configured)
- [ ] Bot token stored in keyring (not in YAML or env vars)
- [ ] `redact_pii: true` in defaults (default value)
- [ ] `access_scope: family` for family group chats
- [ ] `listener_host` set on multi-machine setups
- [ ] Channel PIN set in keyring if sensitive domain access needed
- [ ] Bot set to privacy mode in BotFather (only responds to commands)

---

## Platform Format Adaptation

Each channel adapter is responsible for translating Artha's internal plain-text format into the platform's native formatting. The `ChannelMessage.text` field contains platform-agnostic text (emoji indicators, plain punctuation). When `send_message()` is called, the adapter renders it.

| Platform | Format | Character limit | Notes |
|----------|--------|-----------------|-------|
| **Telegram** | MarkdownV2 (escaped) | 4 096 chars | All special chars (`. _ * [ ] ( ) ~ \` > # + - = \| { } . !`) must be backslash-escaped. Handled by `_tg_escape()` in `scripts/channels/telegram.py`. |
| **Discord** | Discord Markdown | 2 000 chars | `**bold**`, `_italic_`, code blocks — no escaping of most punctuation. Set `max_push_length: 1800` for safety margin. |
| **Slack** | mrkdwn / Block Kit | 3 000 chars/block | `*bold*`, `_italic_`, angle-bracket links `<url\|text>`. Block Kit objects for rich card formatting. |
| **Signal** | Plain text | No hard limit | No rich formatting. Emoji indicators still work. Keep `max_push_length: 500`. |

### Setting `max_push_length` per channel

```yaml
channels:
  telegram:
    # ...
  discord:
    # ...
    # Discord adapter example — override push length
    # max_push_length is read from defaults unless overridden here
```

The global default (`defaults.max_push_length: 500`) is safe for all platforms. Override per-channel only if you need longer messages.

### Pending push queue

If a channel API is unreachable during push (e.g., Telegram down at 6 AM), `channel_push.py` automatically queues the message:

```
state/.pending_pushes/{channel}_{recipient}_{timestamp}.json
```

On the next catch-up run, pending files are sent first (oldest first), then deleted. **Pending files older than 24 hours are discarded** — stale intelligence is worse than none.

Push deduplication marker: `state/.channel_push_marker_YYYY-MM-DD.json` (12-hour window prevents duplicate briefings across machines on the same day). Marker files older than 7 days are cleaned up automatically.

---

## Discord & Slack

Both Discord and Slack are fully implemented as channel adapters (CONNECT v1.0.0).

### Slack

**Setup:** `python scripts/setup_slack.py`

Slack is configured via two tokens stored in the system keyring:
- **Bot token** (`xoxb-…`): `artha-slack-bot-token` — for sending messages and reading channel history
- **App token** (`xapp-…`): `artha-slack-app-token` — for Socket Mode interactive commands (Layer 2)

Required OAuth scopes (bot):
`chat:write`, `files:write`, `channels:history`, `channels:read`, `groups:history`, `im:history`, `users:read`

Required OAuth scopes (app): `connections:write`

**Capabilities:**
- Layer 1 (Push): `send_message` via `chat.postMessage`, `send_document` via `files.upload`
- Layer 2 (Interactive): Socket Mode WebSocket, parses slash commands, enforces sender whitelist
- Slack connector (`scripts/connectors/slack.py`): ingest workspace messages as Artha records

**Config (`config/channels.yaml`):**
```yaml
slack:
  enabled: false  # Set to true after setup
  credential_key: artha-slack-bot-token
  app_credential_key: artha-slack-app-token
  channels: ["#artha"]
  sender_whitelist: []  # User IDs; empty = any whitelisted workspace member
```

**Action:** `slack_send` — posts messages via `chat.postMessage` (requires human approval)

---

### Discord

**Setup:** `python scripts/setup_discord.py`

Discord uses a single bot token stored in the system keyring as `artha-discord-bot-token`.

**Create a bot:**
1. [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. Bot tab → Add Bot → Reset Token → Copy
3. Enable: **Message Content Intent**, **Server Members Intent**
4. OAuth2 → Bot → Permissions: Read Messages, Send Messages, Attach Files
5. Invite bot to your server via generated URL

**Capabilities:**
- Layer 1 (Push): `send_message` via `channels/{id}/messages`, chunking for >2000 char messages
- Layer 2 (Interactive): Gateway WebSocket (`wss://gateway.discord.gg`), `MESSAGE_CREATE` events, slash command dispatch
- Gateway intents: `GUILD_MESSAGES` (512) + `DIRECT_MESSAGES` (4096) + `MESSAGE_CONTENT` (32768)

**Config (`config/channels.yaml`):**
```yaml
discord:
  enabled: false  # Set to true after setup
  credential_key: artha-discord-bot-token
  channel_id: ""  # Replace with your channel's Snowflake ID
  sender_whitelist: []  # Discord User IDs; empty = allow any
```

