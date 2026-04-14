---
schema_version: "1.0"
domain: career_search
last_updated: ""
updated_by: "artha-career"
sensitivity: high
encrypted: true

campaign:
  status: active
  started: ""
  goal_ref: null
  tag_line: ""
  target_companies: []
  target_locations: []
  dealbreakers: []

archetypes:
  - name: "AI Platform / LLMOps"
    keywords: [observability, evals, pipelines, monitoring, reliability, MLflow, deployment, inference]
  - name: "Agentic / Automation"
    keywords: [agent, HITL, orchestration, workflow, multi-agent, autonomous, tool-use]
  - name: "Technical AI PM"
    keywords: [PRD, roadmap, discovery, stakeholder, product manager, go-to-market, launch]
  - name: "AI Solutions Architect"
    keywords: [architecture, enterprise, integration, design, systems, pre-sales, technical advisor]
  - name: "AI Forward Deployed"
    keywords: [client-facing, deploy, prototype, fast delivery, field, customer engineering]
  - name: "AI Transformation"
    keywords: [change management, adoption, enablement, transformation, center of excellence]

profile:
  cv_path: null
  comp_floor: null
  preferred_seniority: []
  preferred_remote: null

portals: []

scoring_weights:
  cv_match: 0.30
  north_star: 0.20
  compensation: 0.15
  culture: 0.15
  level_fit: 0.10
  red_flags: 0.10

scoring_weights_fallback:
  cv_match: 0.35
  north_star: 0.24
  culture: 0.18
  level_fit: 0.12
  red_flags: 0.11

cv_content_hash: null

summary:
  total: 0
  by_status:
    Evaluated: 0
    PartialEval: 0
    Applied: 0
    Responded: 0
    Interview: 0
    Offer: 0
    Rejected: 0
    Discarded: 0
    SKIP: 0
  last_eval_score: null
  average_score: null
  last_scan_date: null
  new_portal_matches: 0
  data_quality: "ok"
  validation_errors: 0
---

# Career Search

## CV Source
- Primary: `~/.artha-local/cv.md` (outside repo — never committed to git)
- Config: `cv_path` in frontmatter allows override
- Proof points: `~/.artha-local/article-digest.md` (if exists)
- NEVER hardcode metrics — read from cv.md at evaluation time
- SECURITY: `cv.md` and `article-digest.md` MUST be in `.gitignore`.

## Applications

| # | Date | Company | Role | Score | Status | PDF | Report | Notes |
|---|------|---------|------|-------|--------|-----|--------|-------|

## Pipeline
<!-- Pending URLs from portal scans — evaluate with /career eval <URL> -->

## Story Bank
<!-- INDEX: -->

