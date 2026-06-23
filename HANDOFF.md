# Session Handoff
# Auto-generated at session end. Read at next session start for continuity.

## Last Session: 2026-06-23 ~15:00 UTC

### What I Changed

**Chat window frontend audit — 4 fixes across 3 files**

Root cause: Audit of chat window components revealed silent prop drop, unbounded array growth, and DOM layout shifts.

Fixes:

1. **RoundedFrame title prop silently ignored (RoundedFrame.tsx)** — `title` prop defined in interface but destructured away in function params. All panel titles (Tools, Thinking, Chat, Sub-agents) were not rendering. Now renders `<div className="frame-title">`.

2. **Chat blocks unbounded growth (App.tsx)** — `setBlocks` appended without limit, causing memory bloat over long sessions. Now capped at 200 via `prev.slice(-199)` on all 4 append call sites.

3. **Thinking blocks unbounded growth (App.tsx)** — Same issue, capped at 100 via `prev.slice(-99)`.

4. **DeferredMarkdown DOM element switch (DeferredMarkdown.tsx)** — Before markdown parse: `<pre>` wrapper. After parse: `<div>` wrapper. This caused a DOM element type switch and layout shift. Now always uses `<div>` wrapper; inner content uses `<pre>` for unparsed text, `<ReactMarkdown>` for parsed.

Skipped (intentional design):
- StreamingMessage pre↔markdown toggle: cheap `<pre>` during active stream, `<ReactMarkdown>` on settle (80ms throttle)
- TerminalBlock elapsed clock: running-only is intentional UX indicator

### Previous Changes This Session
- **Frontend tool panel audit** — 7 fixes (tool_end warns, tool_output race buffering, card cap at 50, CSS data-enter animation, lastIndexOf args parsing, icon crossfade, TS check)

### What's Pending
- Pre-existing TS errors in AgentTree.tsx, AnsiBlock.tsx, AstResult.tsx — not addressed
- No other pending items

### Modified Files
- mini_agent_electron/renderer/src/components/RoundedFrame.tsx (+1 line)
- mini_agent_electron/renderer/src/App.tsx (+12 -12 lines)
- mini_agent_electron/renderer/src/components/DeferredMarkdown.tsx (+22 -21 lines)
- CHANGELOG.md (entry added)
- STATE.txt (entry added)
