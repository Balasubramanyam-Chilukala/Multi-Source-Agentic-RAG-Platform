# ADR 0002 — LangGraph for the agent workflow

- Status: Accepted (2026-10-24); implemented in Phase 3

## Context
The agent must decide between retrieving, refining, clarifying, answering, and abstaining. It needs
bounded retries, per-node telemetry, and conversation state that survives restarts.

## Decision
Model the workflow as an explicit LangGraph `StateGraph` with a typed state, one node per stage
(validate → decide → refine → retrieve → evaluate evidence → generate → validate citations →
persist), and conditional edges. Use the PostgreSQL checkpointer (psycopg 3, shared with the app)
for short-term memory keyed by conversation ID.

## Consequences
- Retry and generation budgets live in the state and are enforced by routing functions.
  Unbounded loops are structurally impossible.
- Each node can be unit-tested with mocked providers. The executed-node list becomes the run trace
  stored in `agent_runs.node_trace`.
- LangChain integrations are used only where they add value. Provider calls go through our own
  adapters (ADR 0003).
