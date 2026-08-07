# LH2 Automation

### The AI-powered operating system for publisher partnerships

LH2 Automation turns the scattered work of finding media partners, researching decision-makers, writing outreach, and following up into one intelligent workflow.

Instead of moving between spreadsheets, inboxes, research tabs, and calendar reminders, a partnerships team can run the full journey from a single command center: discover publishers, qualify accounts, enrich contacts, create highly contextual outreach, and act on replies at the right moment.

> Built to make partnership outreach feel less like administration and more like a repeatable growth engine.

---

## The problem it solves

Publisher outreach is usually fragmented. Context lives in spreadsheets, research is manual, personalization is slow, and the follow-up process breaks down as volume rises.

LH2 brings that work together. It gives teams a structured publisher CRM, a research workspace, AI-guided outreach planning, Gmail-connected execution, and a timeline that keeps every next action visible.

## Feature Deep Dive

### 1. Publisher CRM built for partnership decisions

LH2 organizes both independent publishers and media-house brands in one searchable CRM. Teams can view account context, pipeline state, campaigns, contacts, research status, traffic and syndication signals, and the next planned outreach step without having to reconstruct the story from separate systems.

The dashboard is designed for action, not passive record keeping. A contextual account drawer brings the relevant research, contacts, campaign history, and upcoming sequence activity together so a strategist can decide what to do next from the record itself.

### 2. Intelligent bulk imports with human control

The import workflow accepts raw text, CSV, and XLSX files, then normalizes and deduplicates domains before they reach the CRM. AI-assisted classification identifies whether a domain is an independent publisher or belongs to a media house, while ownership verification helps validate uncertain cases.

Crucially, the product does not treat AI output as an automatic commit. A deterministic policy layer evaluates confidence and evidence, flags ambiguous records for review, supports per-row overrides, attaches campaigns at import time, records decision metadata, and can undo a committed import. It is a safer model for turning messy prospect lists into trusted pipeline data.

### 3. Deep Research: turning a domain into partnership intelligence

Deep Research is a core LH2 workflow. It transforms a company or advisor record into a structured intelligence workspace that can be used directly by the person planning outreach.

The workspace brings together research artifacts, company context, platform presence, social and analytics signals, Notion notes, and relevant knowledge from Google Drive. Research can be organized into readable sections, edited in place, extended with follow-up research commands, and saved back as a reusable artifact rather than disappearing into a one-off chat.

This is where LH2 moves beyond generic AI copywriting. The goal is to build a durable account brief: who the publisher is, what they do, where the opportunity sits, what signals matter, and how LH2 should approach them. That context then flows into recipient research, offering recommendations, and the outreach strategist.

### 4. AI strategist for contextual outreach planning

With research and CRM context available, the Strategist can generate personalized multi-step outreach sequences instead of isolated email drafts. It supports streaming chat, full plan generation, draft refinement, response-aware replanning, and the ability to adapt later drafts when an earlier message changes.

The output is operational: approved plans can be committed to the action timeline as new, appended, overwritten, or multi-step sequences. This gives users a practical bridge from AI strategy to work that the team can execute and track.

### 5. Action timeline, calendar, and follow-up discipline

LH2 turns outreach plans into scheduled actions with sequence-aware dates, status tracking, and history. Teams can see activity in action, timeline, calendar, and history views rather than relying on individual reminders or inbox memory.

Scheduling is flexible without becoming chaotic. AI-generated day offsets can create an initial cadence, deterministic fallback rules maintain a sensible sequence, and changing a next-step date can shift the remaining sequence with it. Plans can also be exported as ICS events for calendar workflows.

### 6. Gmail workflows that close the execution loop

The Gmail integration connects planning to delivery. Users can prepare and save drafts, inject deck links into outreach, synchronize sent mail and inbox activity, and connect responses back to the original outreach thread.

That correlation matters: a reply is not just another email. It becomes a signal inside the CRM and sequence workflow, giving the strategist the context needed to update the next action or replan the approach.

### 7. Contact intelligence and enrichment

Strong outreach depends on reaching the right person. LH2 can extract contacts from research, perform LinkedIn lookups, and enrich details with SignalHire. Selected contacts are persisted against the account and remain available to the research and strategist workflows.

The result is a more complete path from company discovery to a credible, well-targeted first touch.

### 8. Campaign visibility and controlled access

Campaign tagging makes it possible to group imported accounts and follow activity across a partnership initiative. JWT authentication, configurable allowed-user access, and role-based permissions support distinct admin, strategist, and viewer responsibilities across the platform.

## Product Flow

```text
Import publisher domains
        ↓
Classify + verify + review records
        ↓
Research companies and decision-makers
        ↓
Generate a contextual outreach sequence
        ↓
Schedule, draft, and send through Gmail
        ↓
Track replies, outcomes, and next actions
```

## Built as a Full-Stack Product

```text
React + TypeScript + Vite
            ↓
FastAPI REST API
            ↓
Business services + AI/integration layer
            ↓
SQLite for local use or PostgreSQL for shared deployment
```

**Frontend:** React, TypeScript, Vite, React Router  
**Backend:** Python, FastAPI, SQLAlchemy, Uvicorn  
**Intelligence & integrations:** Anthropic, Gmail, Google Drive, Notion, SignalHire, web research  
**Product foundations:** authentication, role-based access, asynchronous workflows, audit-friendly import decisions

## Why this project stands out

LH2 is more than an AI wrapper around email generation. It tackles the full operational loop around partnership growth:

- It combines deterministic workflow controls with AI assistance, rather than trusting a model to make unreviewed data decisions.
- It turns generated strategy into concrete, scheduled work.
- It integrates research, CRM context, communication, and outcomes so the next best action has the information it needs.
- It demonstrates real product engineering across data modeling, backend orchestration, external integrations, and a multi-workspace frontend.

## Run Locally

```bash
# Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev
```

The frontend runs on `http://127.0.0.1:3002` and proxies API requests to `http://127.0.0.1:8000` by default. LH2 uses local SQLite when `DATABASE_URL` is not set; add PostgreSQL and integration credentials in `.env` or `keys.env` as needed.

## Resume Version

> Built an AI-powered publisher partnership command center with React, FastAPI, SQLAlchemy, and Anthropic. Designed end-to-end workflows for domain qualification, publisher CRM, research-driven outreach planning, contact enrichment, Gmail synchronization, campaign tracking, and role-based access control.

## Project Map

| Path | Purpose |
| --- | --- |
| `frontend/` | React command-center experience |
| `api/` | FastAPI routes, auth, schemas, and orchestration |
| `services/` | CRM, import, workflow, Gmail sync, and outreach logic |
| `integrations/` | AI, Gmail, Drive, Notion, enrichment, search, and analytics adapters |
| `models.py`, `db_ops.py` | SQLAlchemy data model and persistence operations |
| `tests/` | Focused workflow regression coverage |

For a technical feature-by-feature walkthrough, see [DEMO_ARCHITECTURE_MAP.md](DEMO_ARCHITECTURE_MAP.md).
