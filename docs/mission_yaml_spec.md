# MERIDIAN Mission YAML Specification

## Overview

A mission in MERIDIAN is defined as a structured YAML workflow. This document describes the YAML format used for mission definitions, including all supported fields and validation rules.

## Top-Level Structure

```yaml
version: "1.0"          # YAML format version (default: "1.0")
mission:                # Mission metadata (required)
  name: string          # Mission name (required, max 255 chars)
  goal: string          # Mission goal / objective (required)
  version: int          # Optional. Current mission version
  status: string        # Optional. "draft" or "published"
agents:                 # Optional. Agent definitions
  - key: string         #   Unique agent key (required)
    name: string        #   Agent display name
    model: string       #   LLM model identifier
    system_prompt: string  # Agent system prompt
steps:                  # Ordered list of steps (required, min 1)
  - key: string         #   Unique step key within mission (required)
    name: string        #   Step display name (required)
    step_type: string   #   "llm", "tool", or "approval" (required)
    agent_key: string   #   Agent reference (required for llm steps)
    prompt_template: string  # LLM prompt template
    tool_refs:          #   Tool references (list of {tool_name, ...})
      - tool_name: string
    approval_required: bool   # Default: false
    max_retries: int    #   Default: 3
    timeout_seconds: int      # Default: 300
    order_index: int    #   Execution order (auto-assigned if omitted)
```

## Field Reference

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `version` | string | No | `"1.0"` | YAML format version |
| `mission.name` | string | **Yes** | — | Mission name |
| `mission.goal` | string | **Yes** | — | Mission goal / objective |
| `mission.version` | int | No | `null` | Current mission version |
| `mission.status` | string | No | `null` | `draft` or `published` |
| `agents[].key` | string | **Yes** | — | Unique agent key |
| `agents[].name` | string | No | — | Agent display name |
| `agents[].model` | string | No | — | LLM model identifier |
| `agents[].system_prompt` | string | No | — | Agent system prompt |
| `steps[].key` | string | **Yes** | — | Unique step key (within mission) |
| `steps[].name` | string | **Yes** | — | Step display name |
| `steps[].step_type` | string | **Yes** | — | `llm`, `tool`, or `approval` |
| `steps[].agent_key` | string | conditional | — | Required for `llm` steps |
| `steps[].prompt_template` | string | No | — | LLM prompt template |
| `steps[].tool_refs` | list | No | `[]` | `[{tool_name, ...}]` |
| `steps[].approval_required` | bool | No | `false` | Requires human approval |
| `steps[].max_retries` | int | No | `3` | Max retry attempts |
| `steps[].timeout_seconds` | int | No | `300` | Step timeout |
| `steps[].order_index` | int | No | index | Execution order |

## Validation Rules

1. **Mission name** — Required, non-empty
2. **Mission goal** — Required, non-empty
3. **Steps** — At least 1 step required
4. **Duplicate step keys** — Rejected
5. **Agent key** — Required for `llm` steps
6. **Tool refs** — Must be a list of objects each containing `tool_name`
7. **Step type** — Must be one of: `llm`, `tool`, `approval`
8. **Duplicate agent keys** — Rejected
9. **No DAG validation in MVP** — Steps execute linearly via `order_index`

---

## Example 1: Simple 3-Step Linear Mission

```yaml
version: "1.0"
mission:
  name: "Product Research Assistant"
  goal: "Research a product category and produce a competitive summary"
  version: 1
  status: draft

agents:
  - key: "researcher"
    name: "Deep Research Agent"
    model: "gpt-4o"

steps:
  - key: "market_overview"
    name: "Market Overview"
    step_type: "llm"
    agent_key: "researcher"
    prompt_template: |
      Provide a high-level overview of the {industry} market including:
      - Market size and growth trends
      - Key players
      - Recent developments
    max_retries: 2
    timeout_seconds: 120

  - key: "competitor_analysis"
    name: "Competitor Analysis"
    step_type: "llm"
    agent_key: "researcher"
    prompt_template: |
      Using the market overview, analyze the top 3 competitors in {industry}:
      - Product positioning
      - Pricing strategy
      - Strengths and weaknesses
    order_index: 1
    max_retries: 3
    timeout_seconds: 120

  - key: "summary_report"
    name: "Generate Summary Report"
    step_type: "llm"
    agent_key: "researcher"
    prompt_template: |
      Produce a final competitive summary report covering:
      - Key findings from market overview
      - Competitive landscape analysis
      - Strategic recommendations
    order_index: 2
    max_retries: 3
    timeout_seconds: 180
```

---

## Example 2: Mission with Approval Gate + Tool References

```yaml
version: "1.0"
mission:
  name: "Content Publication Workflow"
  goal: "Generate, review, and publish a blog post about AI agents"
  version: 3
  status: draft

agents:
  - key: "content_writer"
    name: "Content Writer Agent"
    model: "gpt-4o"
    system_prompt: "You are an expert technical content writer specializing in AI topics."

  - key: "editor"
    name: "Editor Agent"
    model: "gpt-4o-mini"

steps:
  - key: "gather_research"
    name: "Gather Research Data"
    step_type: "tool"
    tool_refs:
      - tool_name: "firecrawl_web_search"
        input:
          query: "AI agent operations platform market trends 2025"
          max_results: 10
    max_retries: 2
    timeout_seconds: 60

  - key: "draft_article"
    name: "Draft Blog Article"
    step_type: "llm"
    agent_key: "content_writer"
    prompt_template: |
      Write a 1000-word blog post about AI agent operations platforms.
      Use the research data from the previous step as source material.
      Structure: introduction, market analysis, key features, conclusion.
    tool_refs:
      - tool_name: "supabase_db_query"
        input:
          table: "research_results"
          query: "SELECT * FROM research_results WHERE run_id = {run_id}"
    max_retries: 3
    timeout_seconds: 300
    order_index: 1

  - key: "editorial_review"
    name: "Editorial Review"
    step_type: "llm"
    agent_key: "editor"
    prompt_template: |
      Review the blog article for:
      - Factual accuracy
      - Grammar and style
      - Engagement quality
      Provide specific revision suggestions.
    max_retries: 3
    timeout_seconds: 120
    order_index: 2

  - key: "human_approval"
    name: "Human Approval Gate"
    step_type: "approval"
    approval_required: true
    prompt_template: |
      Final article draft is ready for publication review.
      Approve to publish, or reject with revision notes.
    max_retries: 0
    timeout_seconds: 86400
    order_index: 3

  - key: "publish_article"
    name: "Publish Article"
    step_type: "tool"
    tool_refs:
      - tool_name: "http_api_call"
        input:
          url: "https://api.publishing-service.com/v1/posts"
          method: "POST"
          headers:
            Content-Type: "application/json"
          body:
            title: "AI Agent Operations: The Missing Platform Layer"
            content_ref: "step:editorial_review"
    max_retries: 2
    timeout_seconds: 60
    order_index: 4
```

---

## JSON <-> YAML Conversion

The API supports creating missions via both JSON and YAML:

### JSON Format

```json
{
  "name": "Product Research Assistant",
  "goal": "Research a product category and produce a competitive summary",
  "steps": [
    {
      "key": "market_overview",
      "name": "Market Overview",
      "step_type": "llm",
      "agent_key": "researcher",
      "prompt_template": "Provide a high-level overview...",
      "max_retries": 2,
      "timeout_seconds": 120
    }
  ]
}
```

### YAML Format (POST body)

```json
{
  "yaml_text": "version: '1.0'\nmission:\n  name: 'Product Research Assistant'\n  goal: 'Research a product category'\nsteps:\n  - key: 'market_overview'\n    name: 'Market Overview'\n    step_type: 'llm'\n    agent_key: 'researcher'\n"
}
```

Both formats produce identical mission records. YAML is converted to the same internal JSON representation before storage.
