# MCP (Model Context Protocol) Integration Design for mini_agent

## 1. Overview & Motivation

The **Model Context Protocol (MCP)** is an open standard (JSON-RPC 2.0) that allows
LLM hosts to discover and invoke tools exposed by MCP-compliant servers. Servers
run as subprocesses (stdio transport) or HTTP endpoints (Streamable HTTP/SSE).

**Goal**: Let mini_agent connect to external MCP servers defined in `.mini_agent.toml`,
discover their tools on startup, register them into the existing `_TOOL_DISPATCH`
system, and call them just like native tools — returning `ToolResult` uniformly.

### MCP protocol summary (tools subset)

| Method               | Direction     | Purpose                                      |
|----------------------|---------------|----------------------------------------------|
| `tools/list`         | client→server | Discover available tools (paginated)         |
| `tools/call`         | client→server | Invoke a tool by name + arguments            |
| `tools/list_changed` | server→client | Notification that tool list changed          |
| `initialize`         | client→server | Capability negotiation & version handshake   |

Tool result: `{ content: [{type: "text", text: "..."}], isError: false }`

Transports: **stdio** (subprocess, line-delimited JSON on stdin/stdout) and
**Streamable HTTP** (POST + optional SSE GET). This design focuses on stdio first
because it's simpler (no HTTP server needed); Streamable HTTP is deferred to v2.

---

## 2. Config Schema

### 2.1 TOML additions (`.mini_agent.toml`)

```toml
# --- MCP server definitions ---
# Each [[mcp_server]] block defines one MCP server to connect to.
# Multiple blocks are supported.

[[mcp_server]]
name = "filesystem"                  # unique name, used for namespace prefix
command = "npx"                      # executable (required for stdio)
args = ["-y", "@anthropic/mcp-server-filesystem", "/tmp"]
env = {}                             # optional extra env vars
cwd = ""                             # optional working directory
enabled = true                       # set false to disable without removing

[[mcp_server]]
name = "postgres"
command = "uv"
args = ["run", "mcp-server-postgres"]
env = { "DATABASE_URL" = "postgres://localhost/mydb" }
enabled = true
```

### 2.2 Config model additions (`config.py`)

```python
@dataclass
class McpServerConfig:
    """One MCP server definition from TOML."""
    name: str                           # unique, e.g. "filesystem"
    command: str                        # executable
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""                       # "" means inherit workspace
    enabled: bool = True

# Added to AgentConfig:
@dataclass
class AgentConfig:
    # ... existing fields ...
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
```

### 2.3 TOML parsing

`_apply_toml()` already iterates `data.items()`. The `[[mcp_server]]` TOML syntax
produces a key `mcp_server` with a list of dicts. We add handling:

```python
# In _apply_toml or a new helper:
if "mcp_server" in data:
    for entry in data["mcp_server"]:
        config.mcp_servers.append(McpServerConfig(
            name=entry.get("name", ""),
            command=entry.get("command", ""),
            args=entry.get("args", []),
            env=entry.get("env", {}),
            cwd=entry.get("cwd", ""),
            enabled=entry.get("enabled", True),
        ))
```

`_TOML_SCHEMA` gains `"mcp_server": list`.

---

## 3. Class Design

### 3.1 `McpConnection` — one per MCP server

File: `tools/mcp_client.py`

```python
class McpConnection:
    """Manages one MCP server connection over stdio.

    Owns the subprocess lifecycle, JSON-RPC message dispatch, and
    reconnection logic.  All I/O is synchronous (thread-based) to
    match mini_agent's existing synchronous tool dispatch.
    """

    def __init__(self, config: McpServerConfig):
        self.config = config
        self.process: subprocess.Popen | None = None
        self._request_id: int = 0
        self._lock = threading.Lock()        # serialize stdin writes
        self._tools: dict[str, dict] = {}    # name → MCP tool schema
        self._connected: bool = False

    # --- Connection lifecycle ---

    def connect(self) -> bool:
        """Spawn subprocess, perform initialize handshake, discover tools.
        Returns True on success, False on failure."""

    def disconnect(self) -> None:
        """Terminate subprocess gracefully (close stdin, wait, then kill)."""

    def reconnect(self) -> bool:
        """Disconnect + connect. Called by manager on failure."""

    # --- MCP protocol ---

    def _send_request(self, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request, read the matching response. Blocking."""

    def _initialize(self) -> bool:
        """Send initialize request with client capabilities.
        Currently: only 'tools' capability."""

    def discover_tools(self) -> dict[str, dict]:
        """Send tools/list, paginate if needed, return {name: schema} dict."""

    # --- Tool execution ---

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        """Send tools/call, wrap result as ToolResult."""
```

**Thread safety**: `McpConnection` is used by one thread at a time (the agent
event loop). The `_lock` protects against accidental concurrent stdin writes.
If we later add async tool calls, we'd need a different model.

### 3.2 Stdio transport details

The stdio transport is line-delimited JSON:

```
→ {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
← {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}
```

Implementation sketch for `_send_request`:

```python
def _send_request(self, method: str, params: dict | None = None) -> dict:
    with self._lock:
        self._request_id += 1
        rid = self._request_id
        request = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(request, ensure_ascii=False) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        self.process.stdin.flush()

        # Read response lines until we get matching id
        while True:
            raw = self.process.stdout.readline()
            if not raw:
                raise ConnectionError("MCP server closed stdout")
            response = json.loads(raw)
            if response.get("id") == rid:
                if "error" in response:
                    raise McpRpcError(response["error"])
                return response.get("result", {})
            # else: notification or response for a different id — queue/discard
```

### 3.3 `McpClientManager` — orchestrates all MCP servers

```python
class McpClientManager:
    """Creates McpConnection instances from config, registers their tools
    into the global dispatch table, and handles reconnection.
    """

    def __init__(self, servers: list[McpServerConfig]):
        self._connections: dict[str, McpConnection] = {}
        for cfg in servers:
            if cfg.enabled:
                self._connections[cfg.name] = McpConnection(cfg)

    def start_all(self) -> list[str]:
        """Connect to all servers, discover tools, register them.
        Returns list of server names that connected successfully.
        On failure: log warning, skip that server.
        """

    def shutdown_all(self) -> None:
        """Disconnect all servers gracefully."""

    def registered_tools(self) -> list[str]:
        """Return list of fully-qualified MCP tool names (mcp/<server>/<tool>)."""

    def call_mcp_tool(self, full_name: str, arguments: dict) -> ToolResult:
        """Parse mcp/<server>/<tool> → call server, return ToolResult."""
```

---

## 4. Tool Dispatch Integration

### 4.1 Naming convention

MCP tools are registered under a namespaced name:

```
mcp/<server_name>/<tool_name>
```

Examples:
- `mcp/filesystem/read_file`
- `mcp/postgres/query`

This avoids collisions with native tools and makes the source clear to the LLM.

### 4.2 Registration into `_TOOL_DISPATCH` and `TOOLS`

On startup, after `init_session()` connects MCP servers:

```python
def _register_mcp_tools(manager: McpClientManager) -> None:
    """Register discovered MCP tools into the global dispatch table and TOOLS list."""
    for server_name, conn in manager._connections.items():
        if not conn._connected:
            continue
        for tool_name, tool_schema in conn._tools.items():
            full_name = f"mcp/{server_name}/{tool_name}"

            # 1. Add to TOOLS (LLM-facing schema)
            TOOLS.append({
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": tool_schema.get("description", ""),
                    "parameters": _convert_mcp_input_schema(tool_schema.get("inputSchema", {})),
                }
            })

            # 2. Register dispatch handler
            _TOOL_DISPATCH[full_name] = _make_mcp_dispatcher(manager, full_name)

            # 3. Register summary
            _TOOL_SUMMARIES[full_name] = _make_mcp_summary(server_name, tool_name)
```

### 4.3 Schema conversion

MCP `inputSchema` uses JSON Schema (2020-12). The mini_agent `TOOLS` list uses
OpenAI function-calling schema. They're both JSON Schema variants but with
minor differences. A converter handles:

| MCP inputSchema                | mini_agent TOOLS parameters         |
|-------------------------------|-------------------------------------|
| `{"type": "object", "properties": {...}, "required": [...]}` | Same structure — pass through |
| `$schema`, `$defs`, `oneOf`   | Strip unsupported keywords         |
| `inputSchema` is `{}` or missing | `{"type": "object", "properties": {}}` |

```python
def _convert_mcp_input_schema(input_schema: dict) -> dict:
    """Convert MCP inputSchema to OpenAI tool parameters schema.
    Strips unsupported JSON Schema keywords and ensures type=object."""
    if not input_schema:
        return {"type": "object", "properties": {}}
    # Copy and strip unsupported keys
    cleaned = {k: v for k, v in input_schema.items()
               if k not in ("$schema", "$defs", "oneOf", "anyOf", "allOf", "not")}
    cleaned.setdefault("type", "object")
    cleaned.setdefault("properties", {})
    return cleaned
```

### 4.4 Dispatcher function

```python
def _make_mcp_dispatcher(manager: McpClientManager, full_name: str):
    """Return a callable that matches the signature of other tool implementations:
    fn(args: dict, write_gate, read_gate) -> ToolResult
    """
    def _dispatch(args: dict, _write_gate, _read_gate) -> ToolResult:
        return manager.call_mcp_tool(full_name, args)
    return _dispatch
```

### 4.5 MCP tool summary

```python
def _make_mcp_summary(server_name: str, tool_name: str):
    def _summary(args: dict) -> str:
        # Show first arg value or arg count
        if args:
            first_key = next(iter(args))
            first_val = str(args[first_key])
            if len(first_val) > 40:
                first_val = first_val[:37] + "..."
            extra = f", +{len(args)-1}" if len(args) > 1 else ""
            return f"mcp/{server_name}/{tool_name}({first_key}={first_val}{extra})"
        return f"mcp/{server_name}/{tool_name}()"
    return _summary
```

---

## 5. Connection Lifecycle

### 5.1 Startup sequence

In `config.init_session()`, after `build_symbol_index()`:

```python
# In init_session():
from tools.mcp_client import McpClientManager, register_mcp_tools

mcp_manager = McpClientManager(config.mcp_servers)
connected = mcp_manager.start_all()
if connected:
    register_mcp_tools(mcp_manager)
    set_context(_mcp_manager=mcp_manager)
```

`start_all()` for each server:
1. Create `McpConnection`
2. `conn.connect()` — spawn subprocess
3. `conn._initialize()` — send `initialize` with capabilities `{"tools": {}}`
4. `conn.discover_tools()` — send `tools/list`, paginate
5. Store tools in `conn._tools`

If any step fails: log warning, mark connection as dead, continue to next server.

### 5.2 Reconnection on failure

When `call_tool()` detects a broken pipe / dead process:

```python
def call_tool(self, name: str, arguments: dict) -> ToolResult:
    try:
        result = self._send_request("tools/call", {"name": name, "arguments": arguments})
        return self._result_to_tool_result(result)
    except (BrokenPipeError, ConnectionError, OSError) as exc:
        # Attempt one reconnect, then fail
        if self.reconnect():
            try:
                result = self._send_request("tools/call", {"name": name, "arguments": arguments})
                return self._result_to_tool_result(result)
            except Exception as exc2:
                return ToolResult(
                    success=False,
                    content=f"MCP server '{self.config.name}' unreachable after reconnect: {exc2}",
                    hint=f"MCP server '{self.config.name}' is down. Try again later or check server configuration.",
                )
        return ToolResult(
            success=False,
            content=f"MCP server '{self.config.name}' disconnected: {exc}",
            hint=f"MCP server '{self.config.name}' is not running. The server may need to be restarted.",
        )
```

Reconnect strategy: one immediate retry, then fail. No exponential backoff (the
LLM can retry on the next turn). If reconnect fails, the connection stays dead
until the agent loop decides to restart (or the server process is restarted externally).

### 5.3 Shutdown

`shutdown_all()` is called at agent exit (or session end). Each connection:
1. Close subprocess stdin → server sees EOF
2. `process.wait(timeout=3)` for graceful exit
3. `process.kill()` if still alive
4. `process.wait()` to reap zombie

### 5.4 Dynamic tool list changes

If an MCP server sends `tools/list_changed` notification, the client should
re-discover tools. Since our client is synchronous and the agent loop is
single-threaded, we can:

- Store the notification as a flag on `McpConnection._tools_changed`
- At the start of each agent turn, check the flag and re-discover if needed
- Re-register tools in `_TOOL_DISPATCH` and `TOOLS`

For stdio transport, notifications arrive on stdout interleaved with responses.
`_send_request` must handle them (queue or process inline). A simple approach:
on every `_send_request`, after reading the matching response, check for any
queued notifications and process them.

---

## 6. Error Handling

### 6.1 Error taxonomy

| Error Source              | Detection                        | ToolResult returned                     |
|---------------------------|----------------------------------|-----------------------------------------|
| Server not in config      | `McpClientManager` lookup        | `success=False`, "Unknown MCP server"   |
| Tool not found on server  | MCP `tools/call` error response  | `success=False`, "Tool not found"       |
| Server process died       | `BrokenPipeError`, `OSError`     | `success=False`, "Server disconnected"  |
| JSON-RPC layer error      | Malformed JSON, bad id           | `success=False`, "Protocol error"       |
| Server returns `isError`  | `isError: true` in result        | `success=False`, content from server    |
| Timeout (future)          | `call_tool` exceeds deadline     | `success=False`, "Tool call timed out"  |
| Config missing `command`  | Validation in `McpServerConfig`  | Logged at startup, server skipped       |

### 6.2 Hint generation

All error paths produce `ToolResult(success=False, content=..., hint=...)` with
actionable hints for the LLM:

```python
def _build_mcp_hint(server_name: str, tool_name: str, error: str) -> str:
    return (
        f"MCP tool 'mcp/{server_name}/{tool_name}' failed: {error}\n"
        f"The tool is provided by an external MCP server. "
        f"Check that the server is running and the tool name is correct. "
        f"Available MCP tools: {_list_mcp_tools()}"
    )
```

### 6.3 Graceful degradation

If no MCP servers are configured, `McpClientManager` is never created.
If a server fails to connect, other servers still work. If all servers fail,
the agent continues with native tools only.

---

## 7. Test Plan

### 7.1 Unit tests (`test_mcp_client.py`)

| Test                                    | What it verifies                                         |
|-----------------------------------------|----------------------------------------------------------|
| `test_mcp_server_config_from_dict`      | TOML parsing produces correct `McpServerConfig`           |
| `test_convert_input_schema_basic`       | `{"type":"object","properties":{...}}` passes through     |
| `test_convert_input_schema_strips_unsupported` | `$schema`, `$defs`, `oneOf` removed               |
| `test_convert_input_schema_empty`       | Missing `inputSchema` → `{"type":"object","properties":{}}` |
| `test_make_mcp_dispatcher_success`      | Dispatcher calls `manager.call_mcp_tool` correctly       |
| `test_make_mcp_dispatcher_error`        | Dispatcher returns `ToolResult(success=False)` on error  |
| `test_result_to_tool_result_success`    | MCP result `{content:[{type:"text", text:"hi"}], isError:false}` → `ToolResult(success=True, content="hi")` |
| `test_result_to_tool_result_error`      | MCP result `{isError: true}` → `ToolResult(success=False)` |
| `test_result_to_tool_result_multi_content` | Multiple content blocks joined with newlines            |
| `test_full_name_parsing`                | `"mcp/filesystem/read_file"` → server="filesystem", tool="read_file" |
| `test_full_name_parsing_invalid`        | Malformed name → error                                   |

### 7.2 Integration tests (`test_mcp_integration.py`)

| Test                                    | What it verifies                                         |
|-----------------------------------------|----------------------------------------------------------|
| `test_connect_to_echo_server`           | Launch a minimal MCP-compatible Python script, connect, list tools |
| `test_discover_tools`                   | Echo server returns predefined tool list, client parses correctly |
| `test_call_tool`                        | Send `tools/call`, get result back as `ToolResult`       |
| `test_reconnect_after_crash`            | Kill the server subprocess, verify `call_tool` reconnects |
| `test_tool_registration`                | Tools discovered from echo server appear in `TOOLS` and `_TOOL_DISPATCH` |
| `test_execute_tool_via_dispatch`        | Call `execute_tool()` with `mcp/test_echo/hello` → ToolResult |
| `test_multiple_servers`                 | Two echo servers with different tools, both registered   |
| `test_server_not_enabled`               | `enabled=false` → no connection attempted                |
| `test_config_missing_command`           | Warning logged, server skipped                           |

### 7.3 Echo server fixture

A minimal MCP-compatible stdio server for testing (no external deps):

```python
#!/usr/bin/env python3
"""Echo MCP server for testing. Reads JSON-RPC from stdin, writes to stdout."""
import json, sys

TOOLS = [
    {"name": "echo", "description": "Echo back the message",
     "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}},
    {"name": "add", "description": "Add two numbers",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}, "required": ["a", "b"]}},
]

def handle_request(req):
    method = req.get("method", "")
    rid = req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {"capabilities": {"tools": {}}}}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        if name == "echo":
            text = args.get("message", "")
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        elif name == "add":
            a, b = args.get("a", 0), args.get("b", 0)
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": str(a + b)}], "isError": False}}
        else:
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"Unknown tool: {name}"}], "isError": True}}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}

for line in sys.stdin:
    req = json.loads(line)
    resp = handle_request(req)
    sys.stdout.write(json.dumps(resp) + "\n")
    sys.stdout.flush()
```

### 7.4 Manual testing checklist

- [ ] Add an `[[mcp_server]]` block to `.mini_agent.toml` pointing to the echo server
- [ ] Start mini_agent, verify server connects, tools appear in Available tools
- [ ] Ask the LLM to use `mcp/test_echo/echo` — verify result
- [ ] Kill the echo server process mid-session, call tool again — verify reconnect
- [ ] Remove the `[[mcp_server]]` block — tools no longer appear

---

## 8. Open Design Questions

1. **Async vs sync**: The Python MCP SDK (`mcp`) is async (anyio). mini_agent
   is synchronous. Building our own sync client is simpler than pulling in
   `anyio` + `mcp` as dependencies. *Decision: write a lightweight sync client.
   If we later need Streamable HTTP or complex features, consider the SDK.*

2. **Caching MCP tools**: Should MCP tool results be cached like native read-only
   tools? Probably not — MCP tools are opaque; we can't know which are read-only.
   The cache key prefix `mcp/` could be exempted from `_CACHEABLE`.

3. **Tool name conflicts**: What if an MCP tool name collides with a native tool?
   The `mcp/<server>/<tool>` prefix prevents this by design. The flat name is
   never registered directly.

4. **Pagination**: MCP `tools/list` supports pagination. Most servers have <100
   tools, so we can fetch all pages on startup. If a server has many tools, we
   can add a config limit.

5. **Streamable HTTP / SSE transport**: Deferred to v2. Stdio covers the common
   case (local MCP servers like filesystem, postgres, etc.). HTTP transport
   requires an async event loop for SSE, which is a bigger change.

6. **Resources and Prompts**: MCP also defines `resources/list`, `resources/read`,
   `prompts/list`, `prompts/get`. These could be exposed as tools (e.g.
   `mcp/<server>/__resource__` and `mcp/<server>/__prompt__`). Deferred to v2.

---

## 9. File Manifest

| File                       | Change                                    |
|----------------------------|-------------------------------------------|
| `tools/mcp_client.py`      | **New** — `McpServerConfig`, `McpConnection`, `McpClientManager`, schema conversion, registration helpers |
| `config.py`                | **Edit** — add `McpServerConfig` import, extend `AgentConfig`, update `_TOML_SCHEMA`, `_apply_toml`, `init_session` |
| `tools/__init__.py`        | **Edit** — import `mcp_client` module (registers nothing by default; registration happens in `init_session`) |
| `DESIGN_MCP.md`            | **New** — this file                        |
| `test_mcp_client.py`       | **New** — unit tests                       |
| `test_mcp_integration.py`  | **New** — integration tests with echo server |
| `test_echo_mcp_server.py`  | **New** — test fixture                     |
| `STATE.txt`                | **Edit** — note MCP integration design     |
