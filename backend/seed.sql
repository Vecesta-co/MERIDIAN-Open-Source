-- ============================================================================
-- MERIDIAN Seed Data — Demo Mission
-- ============================================================================
-- Creates a demo 3-step mission that validates the schema and provides
-- a starting point for Phase 1 development.
-- Run after migrations: psql -U postgres -d meridian -f seed.sql
-- ============================================================================

-- ────────────────────────────────────────────────────────────────────────────
-- 1. Create a demo mission
-- ────────────────────────────────────────────────────────────────────────────
INSERT INTO missions (id, name, description, state)
VALUES (
    'a0000000-0000-0000-0000-000000000001',
    'Demo: Research & Summarize',
    'A 3-step demo mission that researches a topic, analyzes findings, and generates a summary report.',
    'published'
);

-- ────────────────────────────────────────────────────────────────────────────
-- 2. Create version 1 of the mission
-- ────────────────────────────────────────────────────────────────────────────
INSERT INTO mission_versions (id, mission_id, version_int, yaml_text, compiled_json)
VALUES (
    'b0000000-0000-0000-0000-000000000001',
    'a0000000-0000-0000-0000-000000000001',
    1,
    'name: "Demo: Research & Summarize"
version: 1
steps:
  - key: research
    name: "Research Topic"
    kind: tool
    order: 0
    config:
      tool: web_search
      query: "latest AI agent frameworks 2024"
  - key: analyze
    name: "Analyze Findings"
    kind: llm
    order: 1
    depends_on: [research]
    config:
      model: gpt-4
      temperature: 0.3
  - key: summarize
    name: "Generate Summary"
    kind: llm
    order: 2
    depends_on: [analyze]
    config:
      model: gpt-4
      temperature: 0.5
      max_tokens: 2000',
    '{
        "name": "Demo: Research & Summarize",
        "version": 1,
        "steps": [
            {
                "key": "research",
                "name": "Research Topic",
                "kind": "tool",
                "order": 0,
                "config": {
                    "tool": "web_search",
                    "query": "latest AI agent frameworks 2024"
                }
            },
            {
                "key": "analyze",
                "name": "Analyze Findings",
                "kind": "llm",
                "order": 1,
                "depends_on": ["research"],
                "config": {
                    "model": "gpt-4",
                    "temperature": 0.3
                }
            },
            {
                "key": "summarize",
                "name": "Generate Summary",
                "kind": "llm",
                "order": 2,
                "depends_on": ["analyze"],
                "config": {
                    "model": "gpt-4",
                    "temperature": 0.5,
                    "max_tokens": 2000
                }
            }
        ]
    }'
);

-- ────────────────────────────────────────────────────────────────────────────
-- 3. Create the 3 steps for version 1
-- ────────────────────────────────────────────────────────────────────────────
INSERT INTO steps (id, mission_version_id, step_key, name, kind, order_index, depends_on, config)
VALUES
(
    'c0000000-0000-0000-0000-000000000001',
    'b0000000-0000-0000-0000-000000000001',
    'research',
    'Research Topic',
    'tool',
    0,
    '[]'::jsonb,
    '{"tool": "web_search", "query": "latest AI agent frameworks 2024"}'::jsonb
),
(
    'c0000000-0000-0000-0000-000000000002',
    'b0000000-0000-0000-0000-000000000001',
    'analyze',
    'Analyze Findings',
    'llm',
    1,
    '["research"]'::jsonb,
    '{"model": "gpt-4", "temperature": 0.3}'::jsonb
),
(
    'c0000000-0000-0000-0000-000000000003',
    'b0000000-0000-0000-0000-000000000001',
    'summarize',
    'Generate Summary',
    'llm',
    2,
    '["analyze"]'::jsonb,
    '{"model": "gpt-4", "temperature": 0.5, "max_tokens": 2000}'::jsonb
);

-- ────────────────────────────────────────────────────────────────────────────
-- 4. Register a demo tool
-- ────────────────────────────────────────────────────────────────────────────
INSERT INTO tools (id, tool_name, description, input_schema, output_schema, is_enabled)
VALUES (
    'd0000000-0000-0000-0000-000000000001',
    'web_search',
    'Performs a web search and returns top results with snippets.',
    '{"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}'::jsonb,
    '{"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "url": {"type": "string"}, "snippet": {"type": "string"}}}}'::jsonb,
    TRUE
);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. Create a sample eval definition
-- ────────────────────────────────────────────────────────────────────────────
INSERT INTO eval_definitions (id, name, target, config, threshold)
VALUES (
    'e0000000-0000-0000-0000-000000000001',
    'Summary Quality Check',
    'step',
    '{"type": "llm_as_judge", "prompt": "Rate the quality of this summary from 0 to 1 based on clarity, accuracy, and completeness.", "model": "gpt-4"}'::jsonb,
    0.7
);

-- ────────────────────────────────────────────────────────────────────────────
-- 6. Create a sample secret reference
-- ────────────────────────────────────────────────────────────────────────────
INSERT INTO secrets (id, key_name, storage_type, env_key_name)
VALUES (
    'f0000000-0000-0000-0000-000000000001',
    'OPENAI_API_KEY',
    'env_ref',
    'OPENAI_API_KEY'
);
