# Channels (retired)

The Telegram, Slack and Discord channel bridge was **retired and its code removed** (re-artha §6.11, ADR-007, D-03).
Artha runs only inside a session you start (PULL over PUSH, §1.4): there is no listener, push job, scheduler or
always-on engine, and no free-form chat path into your State Files.

Where each former capability went:

| Former capability | Now |
|---|---|
| Briefing push | The Briefing is presented in your session; email to the household stays a user-started step |
| Approving actions from chat | `activity` lists proposals; approve in the session with exact-payload approval (RA-05) |
| Free-form Q&A over State Files | Ask the assistant you are running; it uses the bounded commands (`beliefs`, `why`, `items`, `goals`) |
| Capture from phone | `capture "text"` in a session; reviewed at the next Catch-up |
| Second-machine data | Hand-run ingest bundles (`bundle-export` / `bundle-enroll`, see `docs/catchup-core.md`) |

`python scripts/preflight.py` fails the "Background footprint" check if any scheduler entry or listener owned by Artha
reappears. Old definitions and installers were removed; `config/channels.example.yaml` is kept only as history.
