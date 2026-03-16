# pii-guard: ignore-file — package init; no personal data
"""
scripts/channels — Artha Channel Bridge (ACB v2.0) adapter package.

Channel adapters implement the ChannelAdapter protocol defined in base.py.
The registry loader (registry.py) instantiates adapters from channels.yaml.

Architecture summary:
  channels.yaml (config) → registry.py (loader) → {platform}.py (adapter)
  channel_push.py (Layer 1 — post-catch-up push)
  channel_listener.py (Layer 2 — interactive daemon)

Ref: specs/conversational-bridge.md §3, §4
"""
