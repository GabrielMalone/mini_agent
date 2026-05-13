# DESIGN_COMMS.md — Richer Inter-Agent Communication for mini_agent

## Current State (Baseline)

The current system provides two tools:

| Tool | Behavior |
|------|----------|
| `agent_message(text, from)` | Appends `{"text":…, "from":…}` to a shared global list `_AGENT_MSGS` under a lock. |
| `agent_read(since?)` | Returns all messages (or those with index ≥ `since`) as plain text lines. |

Messages are unstructured strings. There is no routing, no typing, no subscriptions, and no way for one agent to structured-handoff work to another beyond the final `SubAgentResult` returned to the parent. The `AgentRuntime` class already has docstring placeholders for `inboxes`, `deps`, and `keep_alive` — this design fills those in.

## Design Goals

1. **Typed, schema-validated messages** so agents can reason about what they receive.
2. **Subscription model** — agents declare the message types they consume; the runtime routes only matching messages.
3. **Structured handoffs** — an agent can produce a typed result that another agent (or the parent) subscribes to.
4. **Coordination pattern library** — common multi-agent patterns (fan-out/fan-in, pipeline, map-reduce) available as one-liners.
5. **Backward compatibility** — existing `agent_message` / `agent_read` continue to work; new typed tools are additive.
6. **Progressive migration** — the system can be upgraded in three phases without breaking callers.

---

## 1. Message Type System

### 1.1 Core Types

Every typed message has a `type` field drawn from a registry. New types are registered at import time. The base schema:

```python
@dataclass
class AgentMessage:
    type: str           # from MSG_TYPE_REGISTRY
    sender: str         # task_id of sender
    timestamp: float    # time.monotonic()
    payload: dict       # type-specific structure
    correlation_id: str | None  # links related messages (handoff chains, fan-out)
```

### 1.2 Type Registry

```python
# tools/agent_messages.py  (new module)

MSG_TYPE_REGISTRY: dict[str, dict] = {}

def register_message_type(name: str, schema: dict, description: str) -> None:
    """Register a typed message schema."""
    MSG_TYPE_REGISTRY[name] = {
        "schema": schema,
        "description": description,
    }
```

### 1.3 Built-in Types

| Type | Payload Schema | Use |
|------|---------------|-----|
| `text` | `{"body": str}` | Backward-compat wrapper for old `agent_message`. |
| `handoff.result` | `{"result": dict, "task": str}` | Agent A finishes work, hands structured output to the next stage. |
| `handoff.request` | `{"task": str, "input_schema": dict}` | Agent B requests work to be done. |
| `handoff.ack` | `{"accepted": bool, "reason": str}` | Receiver acknowledges a handoff. |
| `status.heartbeat` | `{"progress": str, "pct": float}` | Progress update from a running agent. |
| `status.error` | `{"error": str, "phase": str}` | Agent hit an unrecoverable error. |
| `coord.fan_out` | `{"items": list, "worker_type": str}` | Parent fans out items to worker pool. |
| `coord.fan_in` | `{"results": list, "worker_count": int}` | Worker sends result back to collector. |
| `coord.sync` | `{"barrier": str, "arrived": int, "total": int}` | Barrier synchronization. |

Each type is registered in `tools/agent_messages.py`:

```python
register_message_type("handoff.result", {
    "result": "object",
    "task": "string",
}, "Structured result from a completed sub-task, handed off to the next agent.")
```

### 1.4 Validation

`AgentMessage` validates on construction:

```python
def __post_init__(self):
    if self.type not in MSG_TYPE_REGISTRY:
        raise ValueError(f"Unknown message type: {self.type}")
    _validate_payload(self.payload, MSG_TYPE_REGISTRY[self.type]["schema"])
```

Errors bubble up as `ToolResult(success=False, hint=…)` in tool dispatch.

---

## 2. Subscription Model

### 2.1 Per-Agent Inboxes

`AgentRuntime` gains per-agent inboxes:

```python
# agent_runtime.py — added to AgentRuntime.__init__
self.inboxes: dict[str, list[AgentMessage]] = {}  # task_id -> messages
self.subscriptions: dict[str, set[str]] = {}       # task_id -> set of message types
```

When an agent is spawned, it can declare subscriptions:

```python
# spawn_agent args gain:
#   subscriptions: list[str]  — message types this agent wants to receive
```

These are passed through `shared_context` as structured JSON or, better, set on the runtime:

```python
def set_subscriptions(self, task_id: str, types: list[str]) -> None:
    with self._lock:
        self.subscriptions[task_id] = set(types)
        if task_id not in self.inboxes:
            self.inboxes[task_id] = []
```

### 2.2 Routing

A new internal function `_route_message(msg: AgentMessage)`:

```python
def _route_message(msg: AgentMessage, runtime: AgentRuntime) -> None:
    """Deliver a message to all agents subscribed to its type."""
    with runtime._lock:
        for task_id, subs in runtime.subscriptions.items():
            if msg.type in subs:
                runtime.inboxes.setdefault(task_id, []).append(msg)
    # Also append to the global flat list for backward compat
    with _AGENT_MSGS_LOCK:
        _AGENT_MSGS.append(msg.to_legacy_dict())
```

### 2.3 Default Subscriptions

If no subscriptions are declared, the agent receives **all** message types (backward-compatible behavior). The parent agent always gets everything.

### 2.4 Reading Inbox

New tool: `agent_inbox(task_id, since?)` — reads the typed inbox for a specific agent. `agent_read` continues to work against the global flat list.

---

## 3. Structured Handoffs

### 3.1 Pattern: Producer → Consumer

```
Agent A (producer)                  Agent B (consumer)
─────────────────                   ─────────────────
spawned with:                       spawned with:
  subscriptions: []                   subscriptions: ["handoff.result"]
                                     
... does work ...                    ... idle / does setup ...
                                     
agent_handoff(                       agent_inbox()
  type="handoff.result",           → receives HandoffResult
  result={...}                      
)                                    ... continues with A's output ...
```

### 3.2 `agent_handoff` Tool

```python
@_register("agent_handoff")
def _agent_handoff(args, wg, rg) -> ToolResult:
    """
    Produce a typed result and route it to subscribed agents.

    Parameters:
        type: str              — message type (default "handoff.result")
        result: dict           — structured result payload
        correlation_id: str    — optional correlation ID
        target: str | None     — if set, deliver only to this task_id
    """
```

If `target` is set, the message is delivered **only** to that agent's inbox (bypassing subscription routing). Otherwise, it goes to all subscribers of that type.

### 3.3 Handoff Workflow

1. Parent spawns Agent A with `subscriptions=[]` (it's a producer).
2. Parent spawns Agent B with `subscriptions=["handoff.result"]` (it's a consumer).
3. Agent A works and calls `agent_handoff(result={...})`.
4. The message is routed to Agent B's inbox.
5. Agent B polls with `agent_inbox()` or `agent_read()` and picks up the result.
6. Agent B continues processing.

### 3.4 Acknowledgment

Agent B can call `agent_handoff(type="handoff.ack", result={"accepted": True})` so Agent A knows the handoff was received. The parent can also monitor via `agent_status` polling.

---

## 4. Coordination Pattern Library

### 4.1 Fan-Out / Fan-In (Map-Reduce)

```
Parent spawns N workers, each subscribes to "coord.fan_out".
Parent broadcasts items via agent_handoff(type="coord.fan_out", result={"items": [...]}).
Each worker picks up its slice, processes, and calls agent_handoff(type="coord.fan_in", result={...}).
Parent collects with collect_any / collect_agent.
```

**New pattern helper** (Python API, not a tool — used by the parent agent in its own code):

```python
# tools/agent_patterns.py (new module)

def fan_out(descriptions: list[str], shared_input: dict, runtime, config, wg, rg) -> list[str]:
    """Spawn N workers from descriptions. Returns list of task_ids."""
    ...

def fan_in(task_ids: list[str], runtime) -> list[SubAgentResult]:
    """Collect all results. Returns list in order."""
    ...
```

These are callable from the parent agent's own Python logic (in `main.py` or the agent loop), or exposed as tools if needed.

### 4.2 Pipeline

```
Agent A → handoff.result → Agent B → handoff.result → Agent C
```

Each stage subscribes to the previous stage's output type. The parent spawns them in sequence, waiting for each handoff before spawning the next. A `pipeline()` helper makes this a one-liner:

```python
def pipeline(stages: list[dict], runtime, config, wg, rg) -> SubAgentResult:
    """
    stages = [
        {"task": "fetch data from API", "subscriptions": []},
        {"task": "transform data", "subscriptions": ["handoff.result"]},
        {"task": "write to file", "subscriptions": ["handoff.result"]},
    ]
    Each stage receives the prior stage's handoff.result via inbox.
    Returns the final stage's result.
    """
```

### 4.3 Barrier Synchronization

```python
def barrier(task_ids: list[str], name: str, runtime) -> bool:
    """Block until all task_ids have sent coord.sync for the given barrier name."""
```

### 4.4 Scatter-Gather

Variant of fan-out where workers get different input slices, processed through `shared_context` at spawn time plus coordination messages.

---

## 5. Backward Compatibility

### 5.1 Old Tools Unchanged

`agent_message(text, from)` and `agent_read(since?)` continue to work exactly as before. Internally:

- `agent_message` creates an `AgentMessage(type="text", payload={"body": text}, sender=from_)`.
- It appends both to the global `_AGENT_MSGS` list AND routes to any agents subscribed to `"text"`.
- `agent_read` reads from the global list as before.

### 5.2 New Tools Are Additive

| New Tool | Purpose |
|----------|---------|
| `agent_handoff(type, result, target?, correlation_id?)` | Typed structured handoff |
| `agent_inbox(task_id, since?)` | Read typed inbox for a specific agent |
| `agent_subscribe(task_id, types)` | Declare/update subscriptions at runtime |

### 5.3 No Breaking Changes to `spawn_agent`

Existing `spawn_agent(task=…, max_turns=…, visible=…)` calls work unchanged. New optional parameter:

- `subscriptions: list[str]` — message types the sub-agent consumes (default: all).

### 5.4 `SubAgentResult` Unchanged

`SubAgentResult` gains no new fields. Typed handoffs live in the message bus, not in the return type.

---

## 6. Module Layout

```
tools/
  agent_ops.py          # existing: spawn, status, collect, message, read, extend
  agent_messages.py     # NEW: AgentMessage dataclass, type registry, validation, routing
  agent_patterns.py     # NEW: fan_out, fan_in, pipeline, barrier helpers

agent_runtime.py        # extended: inboxes, subscriptions, get_inbox(), set_subscriptions()
```

No changes to `sub_agent.py` except importing and using the new inbox reading.
No changes to `tools/__init__.py` except registering new tools.
No changes to `config.py`.

---

## 7. Migration Plan

### Phase 1: Add Type Infrastructure (no behavior change)

1. Create `tools/agent_messages.py` with `AgentMessage`, `MSG_TYPE_REGISTRY`, and `register_message_type`.
2. Register the `"text"` type to wrap old messages.
3. Refactor `_agent_message` internally to create `AgentMessage(type="text", …)` while keeping the external API identical.
4. Add `inboxes` and `subscriptions` dicts to `AgentRuntime.__init__` (empty by default — no routing yet).
5. Run full test suite. Zero behavioral change expected.

**Estimated: 2 files touched (agent_messages.py new, agent_ops.py internal refactor).**

### Phase 2: Add Subscription Routing

1. Add `subscriptions` parameter to `spawn_agent`.
2. Implement `_route_message()` in `agent_messages.py`.
3. Wire `agent_message` to route typed messages to subscribed inboxes.
4. Add `agent_inbox` tool for reading typed inboxes.
5. Add `agent_subscribe` tool for runtime subscription changes.
6. Run tests; verify old `agent_message`/`agent_read` still work.

**Estimated: 3 files touched (agent_ops.py, agent_messages.py, agent_runtime.py).**

### Phase 3: Add Handoffs and Patterns

1. Implement `agent_handoff` tool.
2. Register all built-in handoff/coordination message types.
3. Create `tools/agent_patterns.py` with `fan_out`, `fan_in`, `pipeline`, `barrier`.
4. Add integration tests for fan-out/fan-in and pipeline patterns.
5. Document patterns in `DESIGN_COMMS.md` usage section.

**Estimated: 2 new files, 2 files modified (agent_ops.py, sub_agent.py for inbox polling).**

### Phase 4 (optional): Persistent Agents & Long-Running Coordination

1. Add `keep_alive` set to `AgentRuntime` (already designed as placeholder).
2. Allow agents to outlive a single task — stay running and process multiple handoffs.
3. Add `agent_stop(task_id)` to gracefully terminate a persistent agent.

---

## 8. Testing Strategy

| Phase | Tests |
|-------|-------|
| 1 | Unit tests for `AgentMessage` validation, type registry; existing suite unchanged. |
| 2 | Unit tests for routing logic; integration test: spawn 2 agents, one subscribes to "text", verify message delivery. |
| 3 | Integration tests for fan-out/fan-in (3 workers, collect all), pipeline (2-stage), handoff ack. |
| 4 | Persistent agent receiving 3 handoffs in sequence before termination. |

---

## 9. Open Design Questions

1. **Should inbox messages persist to SQLite?** Currently sub-agents don't use SQLite scratchpads. Inbox messages could be transient (lost on parent exit) or persisted. Recommendation: transient for now; persistence in Phase 4.

2. **Should `agent_inbox` be a blocking or polling tool?** Recommendation: polling with `since` index (like current `agent_read`). Blocking can be added later as `agent_inbox_wait`.

3. **Should the parent agent also have an inbox?** Yes — the parent is a first-class agent in the system and should be able to subscribe and receive typed messages. Its task_id is `"parent"`.

4. **Security/safety considerations?** Typed handoffs pass structured data between agents sharing the same workspace. No new safety concerns beyond what already exists for file writes. The `AgentMessage` validation prevents malformed messages from propagating.

5. **Should we support custom message types at runtime?** Yes — `register_message_type` can be called by agents via a new tool `agent_register_type(name, schema, description)`. This allows emergent coordination patterns without code changes.

---

*Design authored for mini_agent multi-agent subsystem. Phase 1 can begin immediately — it is a pure-additive change with zero risk to existing functionality.*
