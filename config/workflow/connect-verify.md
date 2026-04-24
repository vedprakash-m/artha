### Claim Verification (S-06)
For each claim in the Connect draft:
1. Extract the claim as a searchable query
2. Run against WorkIQ: "Find evidence for: [claim]"
3. Assign verdict: CONFIRMED|PARTIAL|NOT VERIFIED
4. Present all verdicts to user in a table BEFORE finalizing

Session cache: same claim verified max once. Max 10 claims per session.
Timeout: 30s per query. On timeout: NOT VERIFIED (not CONFIRMED).
