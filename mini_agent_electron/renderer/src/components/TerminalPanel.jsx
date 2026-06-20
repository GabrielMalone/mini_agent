import { useState, useRef, useEffect, useCallback } from 'react';
import ShellInput from './ShellInput';

// ---------------------------------------------------------------------------
// TerminalPanel — resizable terminal input area with command history
//
// Shows recently typed commands above the ShellInput, with a drag handle at
// the top edge that lets the user expand the panel up to ~25% of the viewport.
// /sh command output is rendered in the chat area (TerminalBlock), not here.
// ---------------------------------------------------------------------------

const MIN_HEIGHT = 48;   // collapsed: just the input line
const MAX_PCT = 0.25;    // max 25% of viewport
const DEFAULT_PCT = 0.12; // default expanded ~12%

// -- Main component -----------------------------------------------------------

export default function TerminalPanel({
  userCommands = [],      // [{id, text, timestamp}]
  shellOutput = [],       // [{id, command, lines, exitCode, timestamp}]
  inputValue,
  onInputChange,
  onSubmit,
  disabled,
  commandHistory = [],
  isLive = false,
  inputRef,
}) {
  const containerRef = useRef(null);
  const historyRef = useRef(null);
  const [height, setHeight] = useState(null); // null = auto (collapsed)
  const draggingRef = useRef(false);
  const dragStartYRef = useRef(0);
  const dragStartHRef = useRef(0);

  // Compute expanded height from viewport
  const getExpandedHeight = useCallback(() => {
    return Math.max(MIN_HEIGHT * 2, window.innerHeight * DEFAULT_PCT);
  }, []);

  // Expand on first use or when user drags
  const expand = useCallback(() => {
    setHeight((prev) => {
      if (prev === null || prev <= MIN_HEIGHT + 4) {
        return getExpandedHeight();
      }
      return prev;
    });
  }, [getExpandedHeight]);

  const collapse = useCallback(() => {
    setHeight(null);
  }, []);

  // Toggle expand/collapse on double-click of handle
  const handleDoubleClick = useCallback(() => {
    setHeight((prev) => {
      if (prev === null || prev <= MIN_HEIGHT + 4) {
        return getExpandedHeight();
      }
      return null;
    });
  }, [getExpandedHeight]);

  // Drag handlers
  const handleMouseDown = useCallback((e) => {
    e.preventDefault();
    draggingRef.current = true;
    dragStartYRef.current = e.clientY;
    dragStartHRef.current = height ?? MIN_HEIGHT;

    const onMouseMove = (ev) => {
      if (!draggingRef.current) return;
      const delta = dragStartYRef.current - ev.clientY; // drag up = expand
      const newH = Math.max(
        MIN_HEIGHT,
        Math.min(window.innerHeight * MAX_PCT, dragStartHRef.current + delta)
      );
      setHeight(newH);
    };

    const onMouseUp = () => {
      draggingRef.current = false;
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
  }, [height]);

  // Auto-expand and auto-scroll when new commands arrive
  useEffect(() => {
    if (userCommands.length > 0) {
      expand();
    }
    const el = historyRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [userCommands]);

  const isExpanded = height !== null && height > MIN_HEIGHT + 4;

  return (
    <div
      id="terminal-panel"
      ref={containerRef}
      className={`rounded-frame${isLive ? ' live' : ''}`}
      style={height !== null ? { height: `${height}px`, flex: '0 0 auto' } : { flex: '0 0 auto' }}
    >
      {/* Drag handle */}
      <div
        className="terminal-resize-handle"
        onMouseDown={handleMouseDown}
        onDoubleClick={handleDoubleClick}
        title="Drag to resize · Double-click to expand/collapse"
      >
        <div className="terminal-resize-grip" />
      </div>

      <div className="frame-body">
        <div className="frame-content">
          <div className="terminal-inner">
            {/* Command history (only visible when expanded) */}
            {isExpanded && userCommands.length > 0 && (
              <div ref={historyRef} className="terminal-history">
                {userCommands.map((cmd) => (
                  <div key={cmd.id} className="terminal-history-line">
                    <span className="prompt">{'>'}</span>
                    <span className="terminal-history-text">{cmd.text}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Current input */}
            <div id="input-container">
              <span className="prompt">{'>'}</span>
              <ShellInput
                ref={inputRef}
                value={inputValue}
                onChange={onInputChange}
                onSubmit={onSubmit}
                disabled={false}
                placeholder="Type a message, /command, or drop files here..."

                commandHistory={commandHistory}
                autoFocus={true}
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
