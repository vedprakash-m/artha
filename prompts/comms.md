---
schema_version: "1.0"
domain: comms
priority: P0
sensitivity: medium
last_updated: 2026-03-07T22:52:33
---
# Communications Domain Prompt

## Purpose
Catch-all for emails that don't match any other domain, plus track outgoing
communications awaiting response, and flag needed follow-ups.

## Routing Logic
An email routes to comms.md when:
- No domain-specific signature matches
- Content is person-to-person communication (not automated notification)
- Multiple domains involved with no clear primary
- Email requires a response but doesn't fit a specific domain

**Messaging records** (WhatsApp, iMessage) route here automatically via
`source_routes` in routing.yaml. These are not emails — they have:
`contact_name`, `phone`, `direction` (sent/received), `snippet`, `is_group`.

## Extraction Rules
For each comms email, extract:
1. **From**: who sent it?
2. **Subject/gist**: what is it about in one line?
3. **Response needed?**: yes/no/optional
4. **Deadline**: if response needed, is there a time constraint?
5. **Route to domain?**: could this belong in another domain? note it.

For each messaging record (WhatsApp/iMessage), extract:
1. **Contact**: name + phone
2. **Direction**: sent or received
3. **Gist**: topic summary from snippet
4. **Response needed?**: if received and last in thread — likely yes
5. **Cross-domain signal?**: does the message content relate to insurance,
   immigration, finance, kids, etc.? If so, note it for cross-domain routing.

## Alert Thresholds
🟠 **URGENT**:
- Personal email awaiting response for >48 hours that was flagged as needs-response
- Time-sensitive request from a person (not automated)

🟡 **STANDARD**:
- Thread that needs a reply (add to follow-up list)
- Email from known contact not categorized elsewhere

🔵 **LOW** / SUPPRESS:
- Marketing, newsletters, promotional (never surface)
- Automated notifications with no action needed
- CC/BCC on threads where no action is needed

## State File Update Protocol
Read `state/comms.md` first. Then:
1. **Awaiting Response**: add items that the family sent and are waiting on a reply
2. **Follow-Ups Needed**: add emails/messages that require a response from the family
3. **Messaging Summary**: for WhatsApp/iMessage records, update the messaging
   activity section (new conversations, pending replies, group highlights)
4. **Unrouted**: add genuinely unclassifiable emails (note if a domain reclassification is needed)
5. Archive: move to archive when response sent or no longer relevant (>7 days without action)

## Anti-Patterns
- DO NOT surface every unread email here — only those requiring action or response
- DO NOT create entries for auto-pay confirmations, shipping confirmations, or notifications
  that are already being tracked in their respective domain
- If an email can be classified to any other domain, prefer that domain

## Briefing Format
```
### Comms (if any actionable items)
• [Person]: [what they need / sent / awaiting] — [action]

### 📱 Messaging (if WhatsApp/iMessage records present)
• WhatsApp: [N] messages since last catch-up ([N] received, [N] sent)
  - [Contact]: [snippet] — needs reply? [yes/no]
  - [Group name]: [N] messages — highlights: [topic]
• iMessage: [N] messages since last catch-up
  - [Contact]: [snippet] — needs reply? [yes/no]
```
Omit entirely if no actionable comms items. Do not pad with low-signal items.
