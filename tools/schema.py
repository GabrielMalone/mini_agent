#!/usr/bin/env python3
"""
schema.py -- API tool schemas sent to the LLM.

Each entry defines a function that the model can call.
Adding a new tool requires an entry here plus a @_register implementation.
"""

from __future__ import annotations

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "edit_lines",
            "description": "Edit a file by replacing line ranges with hash anchors for reliable matching. Use read_file(hash_lines=True) first to get hash-prefixed output, then construct edits with {from, from_hash, to, to_hash, new_text}. All hash anchors are validated before any edit is applied -- any mismatch rejects the entire batch with a precise error. Edits are applied bottom-up so line numbers refer to the pre-edit file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit",
                    },
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "from": {
                                    "type": "integer",
                                    "description": "Starting line number (1-indexed, inclusive)",
                                },
                                "from_hash": {
                                    "type": "string",
                                    "description": "Expected 3-char hash of the 'from' line",
                                },
                                "to": {
                                    "type": "integer",
                                    "description": "Ending line number (1-indexed, inclusive)",
                                },
                                "to_hash": {
                                    "type": "string",
                                    "description": "Expected 3-char hash of the 'to' line",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "Replacement text (can be multiple lines)",
                                },
                            },
                            "required": [
                                "from",
                                "from_hash",
                                "to",
                                "to_hash",
                                "new_text",
                            ],
                        },
                        "description": "List of edits to apply",
                    },
                },
                "required": ["path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_write",
            "description": "Create or update a todo item for tracking progress. Set content to empty string to delete. Use to track progress on complex multi-step tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Optional: existing todo id to update. Omit to create new.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Todo text. Set to empty string to delete this todo.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional: 'pending' or 'done'. Default: 'pending'.",
                    },
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_read",
            "description": "Read current todo list. Filter by id or status. Use this to check remaining work.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Optional: filter to a specific todo id.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional: filter by 'pending' or 'done'.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Capture a learning or observation to project_knowledge for cross-session persistence. Use when you discover a pattern, workaround, or convention worth remembering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Short topic label for this learning (e.g. 'edit_file whitespace', 'module import pattern')",
                    },
                    "detail": {
                        "type": "string",
                        "description": "The learning itself -- what to remember, the pattern, workaround, or convention.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional: category hint (tool_usage, code_pattern, error_pattern, convention, architecture, workaround, dependency, general). Auto-detected if omitted.",
                    },
                },
                "required": ["topic", "detail"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_core",
            "description": "Manage your persistent core memory (frozen snapshot injected at session start). The agent's durable memory of facts, preferences, conventions, and environment notes. Changes persist to disk immediately but appear in the system prompt NEXT session. Use 'read' to see current snapshot, 'add' to append, 'replace' to rewrite entirely, 'remove' to delete by line number. Hard-capped at ~2,500 chars -- when full, consolidate (merge similar entries, remove stale ones) before adding. Example: memory_core(action='add', content='Python uses ruff for linting')",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Action: 'read' (view current), 'add' (append entry), 'replace' (rewrite entire content), 'remove' (delete line by number).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to add/replace. Required for 'add' and 'replace' actions.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number to remove (1-indexed). Required for 'remove' action.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_session_handoff",
            "description": "Write HANDOFF.md for session continuity. Auto-generates a summary of what changed this session using git diff. Call this before signing off to ensure the next session has context about what you worked on.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pending": {
                        "type": "string",
                        "description": "Optional: what's still pending / incomplete from this session.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional: any additional notes for the next session.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_symbol",
            "description": "Find where a symbol (function, class, method name) is defined in the workspace. Supports Python, JS, TS, JSX, TSX files. Returns file path and line number for each match. Much faster than grep/search_files for symbol lookup. Supports substring matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find (e.g. '_request_with_retry', 'ToolResult'). Supports substring matching.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of one or more files at the given path(s). Use 'paths' (array) for multi-file reads. Use offset and limit for line-range reads on large files. Set hash_lines=True for edit_lines (PREFERRED: hash anchors are unambiguous). Or set include_anchors=True for word anchors used with edit_file anchor mode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to read (use 'paths' for multiple files)",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: list of file paths to read (multi-file batch)",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Optional: 0-indexed line number to start reading from (default: 0).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional: max lines to return (default: 300, absolute max: 1000).",
                    },
                    "line_numbers": {
                        "type": "boolean",
                        "description": "Optional: prefix each line with its line number (e.g. '42: content'). Default: false.",
                    },
                    "hash_lines": {
                        "type": "boolean",
                        "description": "Optional: prefix each line with line number and 3-char content hash (e.g. '42:a1f| content'). Use this before edit_lines to get hash anchors. Default: false.",
                    },
                    "include_anchors": {
                        "type": "boolean",
                        "description": "Optional: prefix each line with a stable word anchor (e.g. 'Apple§def foo():'). Anchors persist across edits -- use these with edit_file(files=[{edits:[{anchor, end_anchor, edit_type, text}]}]) for reliable line targeting. Default: false.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file, overwriting if it exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to write",
                    },
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit files via string matching. DEPRECATED: prefer edit_lines with hash_lines=True — hash anchors are mathematically unambiguous and batch-validated. TWO MODES: (1) Anchor mode: use 'files' array with [{path, edits: [{anchor, end_anchor?, edit_type?, text}]}]. Read files with read_file(include_anchors=True) first. (2) Legacy string mode: use 'path'/'paths' with 'old_string'/'new_string'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit (legacy string mode; ignored if 'files' is provided)",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: list of file paths for batch same-string edit (legacy mode)",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to find and replace (legacy mode)",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "String to replace it with (legacy mode)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Optional: number of occurrences to replace (1 = first only, -1 = all). Default: 1. (legacy mode)",
                    },
                    "preview": {
                        "type": "boolean",
                        "description": "Optional: if true, skip the write and return a unified diff. Default: false.",
                    },
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path to the file",
                                },
                                "edits": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "anchor": {
                                                "type": "string",
                                                "description": "Anchor word + § + expected line content (e.g. 'Apple§def foo():')",
                                            },
                                            "end_anchor": {
                                                "type": "string",
                                                "description": "Optional: end anchor for multi-line replacements",
                                            },
                                            "edit_type": {
                                                "type": "string",
                                                "enum": [
                                                    "replace",
                                                    "insert_after",
                                                    "insert_before",
                                                ],
                                                "description": "Default: 'replace'",
                                            },
                                            "text": {
                                                "type": "string",
                                                "description": "Replacement or insertion text",
                                            },
                                        },
                                        "required": ["anchor", "text"],
                                    },
                                    "description": "Array of edits for this file",
                                },
                            },
                            "required": ["path", "edits"],
                        },
                        "description": "Anchor-based edit mode: array of {path, edits} for multi-file batch editing",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory at the given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the directory to list",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command inside the workspace directory. Returns exit code, stdout, and stderr. Timeout defaults to 60s, max 300s. Use for tests, syntax checks, build tools, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute (e.g. 'python -m pytest test_safety.py -v')",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "Run in background, return immediately with task ID. Use task_status to check.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Bypass the destructive-command guard. Default: false. Required for rm, mkfs, etc.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional: max seconds before timing out (default 60, max 300).",
                    },
                    "stdin": {
                        "type": "string",
                        "description": "Optional: string to pipe to the process's standard input.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a text pattern recursively in files within the workspace. Returns matching lines with file path and line number. Skips hidden directories, binary files, and common VCS/venv dirs. Capped at 200 results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text or substring to search for (case-sensitive by default). If regex is true, treated as a Python regex.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)",
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "If true, treat pattern as a Python regex. Default: false.",
                    },
                    "ignore_case": {
                        "type": "boolean",
                        "description": "If true, case-insensitive search. Default: false.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Optional: restrict search to a single file instead of a directory tree. When set, 'path' is ignored.",
                    },
                    "file_types": {
                        "type": "string",
                        "description": "Optional: comma-separated extensions to filter (e.g. '.py,.ts'). Omit to search all.",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Optional: skip the first N matching results (for pagination). Default: 0.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_ast",
            "description": "Search for structural AST patterns (try_except, async_function, decorator, for_loop, while_loop, if_else, with_block, lambda, class_def, function_def, import) across Python/JS/TS/TSX files using tree-sitter. Returns file:line with code snippet. Use to find error handling, async code, decorated functions, loop structures, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "AST pattern to search for. One of: try_except, async_function, decorator, for_loop, while_loop, if_else, with_block, lambda, class_def, function_def, import.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)",
                    },
                    "file_types": {
                        "type": "string",
                        "description": "Optional: comma-separated extensions to filter (e.g. '.py,.ts'). Omit to search all.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_info",
            "description": "Get metadata about a file or directory at the given path. Returns size, permissions, modification time, type (file/directory), and whether the path exists. For directories also reports child count and total child size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file or directory to inspect",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Run tests in the workspace. Returns structured pass/fail counts and failure details. If 'path' is given, runs only those tests; otherwise runs all. Use background=True to run tests asynchronously and poll with task_status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: specific test file or directory to run (e.g. 'test_tools.py' or 'test_memory.py'). If omitted, runs all tests.",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "If true, run tests in background and return a task_id immediately. Use task_status to poll for completion.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Max seconds before timing out (default 120). Only applies in foreground mode.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": "Search code by meaning using embeddings. Finds code chunks semantically similar to the query, even if they don't share keywords. Good for finding related functionality, similar patterns, or code that 'feels like' something. Indexes files live -- no pre-indexing needed. Returns top 10 matches.\n\nWARNING: PERFORMANCE NOTE: The embedding model is preloaded at session startup in a background thread (~9s, ~80MB RAM) so it's typically ready before you need it. If you call semantic_search very early in a session you may see a brief \"still loading\" message while the background thread finishes. Still, prefer find_symbol (instant, indexed) or search_files (instant, grep) for exact name/text queries. Use semantic_search only when you don't know the function/variable name and grep won't work -- e.g. 'find code that validates user input' or 'locate retry logic patterns'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language description of what to find (e.g. 'error handling around file writes', 'retry logic')",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory to search in (defaults to workspace root)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using Exa. Returns relevant pages with titles, URLs, and highlighted excerpts. Good for documentation lookup, API references, current information, and technical questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query. Be specific and use technical terms for best results.",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5, max 20).",
                    },
                    "search_type": {
                        "type": "string",
                        "description": "Search depth: 'auto' (default, balanced), 'fast', 'deep'. 'auto' works for most queries.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_status",
            "description": "Check the status of a background shell task by its ID. background=True in run_shell returns a task_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID returned by run_shell with background=True",
                    }
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_scratchpad",
            "description": "Write content to the agent's scratchpad -- a persistent working note that survives across turns. Tracks plan, progress, decisions, things tried, and open questions. Shown at start of each turn. Overwrites previous content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Content to write to the scratchpad. Use markdown.",
                    }
                },
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_usages",
            "description": "Find all usages (references) of a Python symbol across the workspace. Returns file path, line number, and surrounding context for each usage. Much faster than grep for symbol references. Use this to find all callers of a function or all places a class/variable is used before refactoring.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find usages of (e.g. 'execute_tool', 'ToolResult').",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callers",
            "description": "Find all callers (functions that call) a given symbol in the workspace. Uses AST-based call graph analysis. Returns callee name, file path, and line number for each caller. Substring matching on symbol name is supported if no exact match is found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find callers of (e.g. '_request_with_retry').",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callees",
            "description": "Find all callees (functions called by) a given symbol in the workspace. Uses AST-based call graph analysis. Returns callee name, file path, and line number. Substring matching on symbol name is supported if no exact match is found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find callees of (e.g. 'execute_tool').",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_related",
            "description": "Find entities directly related to a given symbol in the knowledge graph. Shows callers, callees, imports, and inheritance relationships. Use this to understand how a symbol connects to the rest of the codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to find related entities for.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_path",
            "description": "Find paths between two symbols in the knowledge graph. Given a 'from' and 'to' symbol name, returns the shortest connection paths through call, import, and inheritance edges. Useful for understanding how two parts of the codebase connect.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "string", "description": "Starting symbol name."},
                    "to": {"type": "string", "description": "Target symbol name."},
                },
                "required": ["from", "to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subgraph",
            "description": "Get a subgraph around a symbol, extending N hops in the knowledge graph. Returns entities and edges grouped by relationship kind (call, import, inherit). Useful for exploring a symbol's neighborhood in the codebase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Symbol name to center the subgraph on.",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Number of hops from the symbol (default 2, max 4).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explore",
            "description": "PRIMARY TOOL — call FIRST for understanding how something works or before an edit. Takes a natural-language question (e.g. 'how does auth handle login') or a bag of symbol/file names, finds the relevant symbols, returns their verbatim source code grouped by file with line numbers PLUS call relationships among them — all in one budget-capped response. Much more efficient than a find_symbol/search_files/read_file loop. Budget scales with project size: 3-4 files for small repos, 6-8 for larger ones.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language question OR bag of symbol/file names to explore (e.g. 'AuthService loginUser session-manager', 'how does ToolResult propagate errors', 'renderScene animation loop').",
                    },
                    "max_files": {
                        "type": "integer",
                        "description": "Maximum number of files to include source code from (default: auto-scaled by project size, 1-20).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "verify",
            "description": "Run lint + relevant tests for files modified in the current session. Uses tracked writes/edits to find matching test files. Falls back to running all tests if nothing has been modified yet. Use after code changes to verify nothing broke before moving on.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restore_file",
            "description": "Restore a file from its session backup. Undoes the last write_file or edit_file operation on the given path. Only files modified in the current session can be restored.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to restore from backup",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_turn",
            "description": "Recall a summary of what happened on a previous turn. Use this to recover lost context when old tool results have been pruned from the conversation. Returns tool calls made and their results for the given turn number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "turn": {
                        "type": "integer",
                        "description": "Turn number to recall (1-indexed)",
                    }
                },
                "required": ["turn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "session_search",
            "description": "Full-text search across all past session messages. Use when the user references something from a previous conversation ('we fixed this before', 'use the approach from last time'). Returns matching message excerpts ordered by relevance. Uses FTS5 full-text indexing for fast retrieval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms to find in past messages.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 10).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_observation",
            "description": "Record a structured observation about a tool call, discovery, or decision. Observations are typed (bugfix, discovery, decision, refactor, other), tagged with concepts, linked to files, and persist across sessions. Content-based deduplication prevents duplicates. Use this to remember important discoveries, design decisions, bugfixes, and patterns. Provide 'narrative' (paragraph) or 'facts' (bullet points), or both.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Observation type: 'bugfix', 'discovery', 'decision', 'refactor', or 'other'.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title summarizing this observation.",
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Optional subtitle with additional context.",
                    },
                    "narrative": {
                        "type": "string",
                        "description": "Paragraph narrative describing what happened and why it matters.",
                    },
                    "facts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Bullet-point facts extracted from this observation.",
                    },
                    "concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags/concepts for filtering (e.g. 'architecture', 'performance', 'security').",
                    },
                    "files_read": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths that were read.",
                    },
                    "files_modified": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "File paths that were modified.",
                    },
                    "tool_name": {
                        "type": "string",
                        "description": "Name of the tool that triggered this observation (if auto-captured).",
                    },
                },
                "required": ["type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_session_summary",
            "description": "Write a structured session summary for cross-session continuity. The summary is injected into future sessions so the agent picks up where it left off. Canonical fields: request (what the user asked for), investigated (what was explored), learned (key discoveries), completed (what was accomplished), next_steps (what remains). Also supports files_read and files_edited lists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request": {
                        "type": "string",
                        "description": "What the user asked for this session.",
                    },
                    "investigated": {
                        "type": "string",
                        "description": "What was looked into / explored.",
                    },
                    "learned": {
                        "type": "string",
                        "description": "Key discoveries and insights gained.",
                    },
                    "completed": {
                        "type": "string",
                        "description": "What was accomplished / delivered.",
                    },
                    "next_steps": {
                        "type": "string",
                        "description": "What remains to be done.",
                    },
                    "files_read": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files that were read during the session.",
                    },
                    "files_edited": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Files that were edited during the session.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional additional notes for the next session.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_observations",
            "description": "Query structured observations from the session database. Filter by type (bugfix, discovery, decision, refactor, other), concepts (tags), or session_id. Returns observations ordered by recency with token economics. Use this to review past discoveries, decisions, or bugfixes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by observation types (e.g. ['discovery', 'decision']).",
                    },
                    "concepts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by concepts/tags (e.g. ['architecture', 'performance']).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (default 20, max 100).",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default 0).",
                    },
                    "session_id": {
                        "type": "string",
                        "description": "Filter by specific session ID.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch a web page URL and return its text content (truncated). Supports text/html and text/plain content types. Use this to read documentation, API references, or any web page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch (must be http:// or https://)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional: request timeout in seconds (default 15, max 30).",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Optional: max characters to return (default 10000).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "Declare a structured task plan with numbered steps. Overwrites any previous plan. Use before multi-step work so progress is tracked. Shown at start of each turn until all steps complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ordered list of step descriptions (e.g. ['Read config.py', 'Add new option', 'Update tests']).",
                    }
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_status",
            "description": "Mark a plan step complete, or view current plan progress. No args: see plan and which steps are done. With 'step' (1-indexed): mark that step complete.",
            "parameters": {
                "type": "object",
                "properties": {
                    "step": {
                        "type": "integer",
                        "description": "Optional: 1-indexed step number to mark complete. Omit to just view current plan status.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "session_stats",
            "description": "Show session statistics: turns used, context tokens, active sub-agents, plan progress. No parameters needed.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_definition",
            "description": "Go to definition using the Language Server Protocol. Given a file path and a position (line, character), returns the location(s) where the symbol is defined. Requires pylsp for Python or typescript-language-server for JS/TS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to query.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "0-indexed line number of the symbol.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character offset within the line.",
                    },
                },
                "required": ["file_path", "line", "character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_references",
            "description": "Find all references to a symbol using the Language Server Protocol. Given a file path and position, returns all locations that reference the symbol. Requires pylsp for Python or typescript-language-server for JS/TS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to query.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "0-indexed line number of the symbol.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character offset within the line.",
                    },
                    "include_declaration": {
                        "type": "boolean",
                        "description": "Whether to include the declaration itself in results. Default: true.",
                    },
                },
                "required": ["file_path", "line", "character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_hover",
            "description": "Get hover information (type, docs, signature) for a symbol using the Language Server Protocol. Given a file path and position, returns documentation for the symbol at that location. Requires pylsp for Python or typescript-language-server for JS/TS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to query.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "0-indexed line number of the symbol.",
                    },
                    "character": {
                        "type": "integer",
                        "description": "0-indexed character offset within the line.",
                    },
                },
                "required": ["file_path", "line", "character"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lsp_diagnostics",
            "description": "Get diagnostics (errors, warnings, hints) for a file using the Language Server Protocol. Opens the document and collects published diagnostics. Requires pylsp for Python or typescript-language-server for JS/TS.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to check for diagnostics.",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_image",
            "description": "Read an image file, send it to GPT-4o, and return a text description of what the model sees. Use this to understand images, screenshots, diagrams, or photos. Use the 'prompt' parameter to ask a specific question about the image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the image file to describe.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Optional: a specific question or instruction about the image (e.g. 'What error message is shown?', 'Read all text in this screenshot'). Default: general description.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diagnose_failures",
            "description": "Read the last test run output from memory store, parse for FAILED lines, extract test function names and file paths, read the relevant source files, and return a structured failure summary with code snippets. No parameters needed -- reads automatically from the persisted test output.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "init",
            "description": "Analyze the workspace and auto-generate .mini_agent.rules (coding conventions, module map) and .mini_agent.toml (if missing). Also seed project_knowledge with auto-detected learnings about the codebase structure. Use this on first run or when the project structure has changed significantly.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open the user's default browser to the given URL. Opens in a new tab and returns immediately -- does not wait for the page to load. For programmatic browser interaction, use the browser_* tools instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to open. Must start with http:// or https://.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate a headless browser (Playwright Chromium) to a URL. Returns the page title and final URL after redirects. Requires playwright to be installed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to. Must start with http:// or https://.",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_snapshot",
            "description": "Capture the accessibility tree of the current browser page. Returns a structured text representation of interactive elements (roles, names, states) -- much more compact and LLM-friendly than raw HTML or a screenshot. Use this to understand what's on the page before clicking or typing.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element on the current browser page identified by its accessibility role and name. Use browser_snapshot first to see available elements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "ARIA role of the element (e.g. 'button', 'link', 'textbox', 'checkbox')",
                    },
                    "name": {
                        "type": "string",
                        "description": "Accessible name of the element (visible text or aria-label)",
                    },
                },
                "required": ["role", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into an input element on the current browser page identified by its role and name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "ARIA role (typically 'textbox' or 'searchbox')",
                    },
                    "name": {
                        "type": "string",
                        "description": "Accessible name (label text, placeholder, or aria-label)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to type into the element",
                    },
                },
                "required": ["name", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Capture a full-page PNG screenshot of the current browser page. Saves to the workspace so it can be inspected with read_image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path within workspace to save the screenshot (default: browser_screenshot.png)",
                    },
                    "full_page": {
                        "type": "boolean",
                        "description": "Capture the full scrollable page (default: true)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_discover",
            "description": "List all tools from all connected MCP (Model Context Protocol) servers. Use this to see what external tools are available before calling them with mcp_call.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mcp_call",
            "description": "Call a tool on a specific MCP (Model Context Protocol) server. Use mcp_discover first to see available servers and tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "MCP server name (e.g. 'my-server'). Use mcp_discover to see available servers.",
                    },
                    "tool": {
                        "type": "string",
                        "description": "Tool name to call on the server (e.g. 'calculate', 'get_weather').",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Optional: keyword arguments to pass to the MCP tool.",
                    },
                },
                "required": ["server", "tool"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_snapshot",
            "description": "Capture the accessibility tree of the frontmost desktop window. Returns a structured text representation of interactive elements (roles, names, states) -- much more compact and LLM-friendly than a screenshot. Use this to understand what's on screen before clicking or typing in native desktop apps. On macOS, requires Accessibility permission (System Settings -> Privacy -> Accessibility -> enable Terminal). On Windows, requires: pip install uiautomation.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_click",
            "description": "Click a native desktop UI element identified by its role and name. Use desktop_snapshot first to see available elements. Supports macOS (via Accessibility API) and Windows (via UI Automation). Args: role (e.g. 'button', 'textfield', 'checkbox', 'menuItem'), name (visible text or label).",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "Element role (e.g. 'button', 'textfield', 'checkbox', 'menuItem', 'tab', 'link', 'window'). See desktop_snapshot output for available roles.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Accessible name of the element (visible text, label, or aria-label equivalent).",
                    },
                },
                "required": ["role", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_type",
            "description": "Type text into the currently focused native desktop field. Click into the target field first (using desktop_click or manually), then call this to type. Args: text (string to type).",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to type into the focused field.",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_find",
            "description": "Find native desktop UI elements matching a text or role query across all open windows. Args: query (text to search for in element names/labels), role (optional role filter like 'button', 'window', 'menu').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for in element names/labels. Case-insensitive partial match.",
                    },
                    "role": {
                        "type": "string",
                        "description": "Optional: filter by role (e.g. 'button', 'window', 'menu'). Omit to search all roles.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_screenshot",
            "description": "Capture a PNG screenshot of the native desktop (not browser). Unlike browser_screenshot, this captures any open application, menubar, dock, taskbar, etc. Saves to a temp directory. Use read_image to view it. Requires: pip install mss.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_apps",
            "description": "List all running desktop applications with PID, name, bundle ID, and foreground status. Use this to see what's running before launching, quitting, or focusing apps.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_launch",
            "description": "Launch a macOS application by name (e.g. 'Safari', 'Terminal', 'Visual Studio Code') or bundle ID (e.g. 'com.apple.Safari'). Uses 'open -a' under the hood.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Application name (e.g. 'Safari', 'Finder', 'Terminal') or bundle ID (e.g. 'com.apple.mail').",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_quit",
            "description": "Quit a macOS application by name (e.g. 'Safari') or PID. Tries gentle quit first (osascript), then falls back to pkill for force-quit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Application name (e.g. 'Safari', 'Terminal') or PID string.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_focus",
            "description": "Bring a macOS application window to the foreground (activate it). Use after desktop_launch or to switch between running apps.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Application name to activate (e.g. 'Safari', 'Terminal').",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_clipboard",
            "description": "Read from or write to the macOS system clipboard. Use action='read' to get current clipboard content, or action='write' with the text parameter to set it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "'read' to get clipboard contents, 'write' to set clipboard to the 'text' parameter.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to copy to clipboard (only when action='write').",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_windows",
            "description": "List all visible windows across all macOS applications using CGWindowList. Shows window titles, owner apps, sizes, positions, and PIDs. Much more comprehensive than desktop_snapshot which only covers the frontmost app.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_system_info",
            "description": "Gather macOS system metrics: hostname, OS version, CPU cores, physical memory, disk usage, battery status, thermal state, load average, and system uptime. All in one call.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_key",
            "description": "Press a macOS key combination via CGEvent (or AppleScript fallback). Examples: 'cmd+c' (copy), 'cmd+v' (paste), 'cmd+tab' (app switcher), 'cmd+shift+4' (screenshot region), 'escape', 'return', 'space', 'left', 'right', 'f5', 'f11'. Supports modifiers: cmd, shift, option/alt, ctrl.",
            "parameters": {
                "type": "object",
                "properties": {
                    "combo": {
                        "type": "string",
                        "description": "Key combination string (e.g. 'cmd+c', 'cmd+shift+4', 'cmd+tab', 'escape', 'return', 'space', 'left', 'right', 'up', 'down', 'f5', 'f11'). Modifiers: cmd, shift, option/alt, ctrl, fn.",
                    }
                },
                "required": ["combo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_open",
            "description": "Open a file, folder, or URL in the default macOS application. Files open with their associated app, folders open in Finder, URLs open in the default browser. Equivalent to the 'open' command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "File path, folder path, or URL to open (e.g. '/Users/me/doc.pdf', '/Applications', 'https://github.com').",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_reveal",
            "description": "Reveal a file or folder in Finder (opens a Finder window with the item selected). Use this to show the user where a file is located.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file or folder to reveal in Finder.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "desktop_notify",
            "description": "Post a macOS system notification (appears as a banner in Notification Center). Useful for alerting the user when a long-running task completes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Notification title (required).",
                    },
                    "message": {
                        "type": "string",
                        "description": "Notification body text (optional).",
                    },
                    "sound": {
                        "type": "boolean",
                        "description": "Play default notification sound. Default: false.",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "discord_search",
            "description": "Search recent message history across all channels in the connected Discord server. Use this when asked about conversations, links, or information shared in Discord. Returns matching messages with channel name, author, timestamp, snippet, and jump URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword or phrase to search for in Discord messages.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Optional: max results to return (default 15, max 30).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_skeleton",
            "description": "Read the structural outline of one or more files by extracting classes, functions, and methods with their line signatures while stripping all implementation logic. Use this to quickly understand file structure before requesting specific functions. Use 'include_anchors=true' to get stable word anchors for use with edit_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths to analyze",
                    },
                    "path": {
                        "type": "string",
                        "description": "Single file path (alternative to 'paths')",
                    },
                    "include_anchors": {
                        "type": "boolean",
                        "description": "If true, prefix each definition line with a stable word anchor (e.g. 'Apple§def foo():') for use with edit_file. Default: false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_function",
            "description": "Retrieve specific function or class bodies from a file with stable word anchors. More token-efficient than read_file for surgical edits -- only returns the requested symbols. Use include_anchors=true to get anchors for edit_file targeting.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of function/class names to retrieve",
                    },
                    "name": {
                        "type": "string",
                        "description": "Single function/class name (alternative to 'names')",
                    },
                    "include_anchors": {
                        "type": "boolean",
                        "description": "If true, prefix each line with a stable word anchor. Default: false.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_symbol",
            "description": "Replace one or more symbols (functions, methods, or classes) in one or more files with new code. More robust than edit_file because it targets specific AST nodes directly by byte range -- no string matching required. IMPORTANT: Provide the COMPLETE replacement including all decorators, docstrings, and export keywords.",
            "parameters": {
                "type": "object",
                "properties": {
                    "replacements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "Path to the file",
                                },
                                "symbol": {
                                    "type": "string",
                                    "description": "Name of the function/class to replace",
                                },
                                "text": {
                                    "type": "string",
                                    "description": "Complete replacement code",
                                },
                                "type": {
                                    "type": "string",
                                    "enum": ["def", "class"],
                                    "description": "Optional: disambiguate if name is ambiguous",
                                },
                            },
                            "required": ["path", "symbol", "text"],
                        },
                        "description": "Array of symbol replacements to apply",
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to file (single replacement alternative to 'replacements')",
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Symbol name (single replacement mode)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Replacement code (single replacement mode)",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["def", "class"],
                        "description": "Optional: symbol type for disambiguation",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rename_symbol",
            "description": "Renames ALL occurrences of a symbol (function, class, method, or variable) inside the specified files or directories. Uses tree-sitter AST to identify symbols precisely -- more accurate than a simple search-and-replace because it understands Python and TypeScript language structure. For renaming tasks, strongly prefer this as the first pass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "existing_symbol": {
                        "type": "string",
                        "description": "The exact name of the symbol to be renamed.",
                    },
                    "new_symbol": {
                        "type": "string",
                        "description": "The new name for the symbol.",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "An array of relative paths to the directories or files to perform the rename in.",
                    },
                },
                "required": ["existing_symbol", "new_symbol", "paths"],
            },
        },
    },
]
