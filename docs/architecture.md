# MERIDIAN Architecture

## Overview

MERIDIAN is a self-hosted, open-source AI Agent Operations Platform that provides a unified environment to design, run, observe, and trust AI agents — without writing a framework.

## System Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                        MERIDIAN PLATFORM                              │
│                                                                       │
│  ┌─────────────────────┐     ┌───────────────────────────────────┐   │
│  │   MISSION DESIGNER  │────▶│           AGENT RUNTIME           │   │
│  │  (YAML + UI Editor) │     │  (Orchestrator + State Machine)   │   │
│  └─────────────────────┘     └────────┬──────────────┬───────────┘   │
│                                       │              │               │
│                          ┌────────────▼───┐  ┌───────▼────────────┐ │
│                          │  TOOL SANDBOX  │  │   APPROVAL GATE    │ │
│                          │ (Isolated Exec)│  │ (Human-in-the-Loop)│ │
│                          └────────────┬───┘  └───────┬────────────┘ │
│                                       │              │               │
│                          ┌────────────▼──────────────▼────────────┐ │
│                          │           TRACE ENGINE                  │ │
│                          │   (Spans, Logs, LLM Calls, Costs)      │ │
│                          └────────────────────┬────────────────────┘ │
│                                               │                      │
│              ┌────────────────────────────────▼──────────────────┐  │
│              │                    STATE STORE                     │  │
│              │      (Postgres via Supabase + Redis Cache)         │  │
│              └────────────────────────────────┬──────────────────┘  │
│                                               │                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────▼──────────────────┐  │
│  │  EVAL SUITE │  │ CONFIG VAULT │  │    MISSION DASHBOARD       │  │
│  │(Auto + Human│  │(Keys/Secrets)│  │ (DAG View, Logs, Inbox)    │  │
│  └─────────────┘  └──────────────┘  └───────────────────────────┘  │
│                                                                       │
│  ──────────────────── INTEGRATION BUS ──────────────────────────────  │
│        N8N │ Firecrawl │ BrowseUse │ Supabase │ Custom API           │
└───────────────────────────────────────────────────────────────────────┘
```

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend API | FastAPI (Python) | Async REST API |
| Database | Supabase (Postgres) | Primary data store |
| Task Queue | Redis + RQ/Celery | Async job execution |
| Trace Storage | Postgres JSONB | Span observability |
| Frontend | Next.js | Mission Dashboard UI |
| Auth | Supabase Auth | Authentication |
| LLM Integration | LiteLLM | Vendor-neutral LLM calls |

## Phase 0 Structure

```
/
├── backend/
│   ├── app/                    # FastAPI application
│   │   ├── api/v1/             # Versioned API routers
│   │   ├── core/               # Config, logging
│   │   ├── db/                 # Database session
│   │   └── models/             # Pydantic schemas
│   ├── migrations/             # SQL migration files
│   └── requirements.txt
├── infra/                      # Docker Compose, infra configs
├── docs/                       # Documentation
└── .env.example
```

## Core Entities

- **Missions**: Top-level agent workflow definitions
- **Mission Versions**: Versioned snapshots of mission YAML/JSON definitions
- **Steps**: Individual actions (LLM call, tool call, approval gate) within a mission
- **Runs**: Execution instances of a mission version
- **Run Steps**: Per-run, per-step execution tracking with status
- **Spans**: OpenTelemetry-style trace records for observability
- **Tools**: Registry of available external tools
- **Approvals**: Human-in-the-loop decision records
- **Eval Definitions**: Automated quality check configurations
- **Eval Results**: Scores and verdicts from eval checks
- **Secrets**: Encrypted credential storage (never plaintext)
