import { memo } from 'react';
import DeferredMarkdown from './DeferredMarkdown';
import StreamingMessage from './StreamingMessage';
import ToolCard from './ToolCard';
import AnsiBlock from './AnsiBlock';

// ---------------------------------------------------------------------------
// TerminalBlock -- Warp-style command block with left notch
//
// Props:
//   block           - { id, command, output, status, timestamp, toolCards?, thinkingBlocks? }
//   streamingOutput - live streaming text (only for running block)
//   isRunning       - true if this block is the active streaming block
//   onEdit(command) - called when user clicks the command area
//   theme           - theme object for ToolCards
// ---------------------------------------------------------------------------

const STATUS_COLORS = {
  ok: 'var(--green)',
  err: 'var(--red)',
  running: 'var(--pulse)',
};

function formatElapsed(ms) {
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  return `${min}m ${sec % 60}s`;
}

const TerminalBlock = memo(function TerminalBlock({
  block,
  streamingOutput,
  isRunning,
  onEdit,
  theme,
}) {
  const { id, command, output, status, timestamp, toolCards, thinkingBlocks } = block;
  const notchColor = STATUS_COLORS[status] || 'var(--dim)';

  const displayOutput = isRunning ? (streamingOutput || output) : output;

  const handleCommandClick = () => {
    if (onEdit) onEdit(command);
  };

  return (
    <div
      className={`terminal-block terminal-block--${status}`}
      data-block-id={id}
    >
      {/* Left notch */}
      <div className="terminal-block__notch" style={{ borderColor: notchColor }} />

      {/* Body */}
      <div className="terminal-block__body">
        {/* Command bar */}
        <div
          className="terminal-block__command"
          onClick={handleCommandClick}
          title="Click to edit and re-run"
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              handleCommandClick();
            }
          }}
        >
          <span className="terminal-block__prompt">$</span>
          <span className="terminal-block__cmd-text">{command}</span>
          {status === 'running' && (
            <span className="terminal-block__elapsed">
              {formatElapsed(Date.now() - timestamp)}
            </span>
          )}
          {status === 'ok' && (
            <span className="terminal-block__check">✓</span>
          )}
          {status === 'err' && (
            <span className="terminal-block__cross">✗</span>
          )}
        </div>

        {/* Output area */}
        {(displayOutput || isRunning) && (
          <div className="terminal-block__output">
            {isRunning && streamingOutput ? (
              <StreamingMessage text={streamingOutput} />
            ) : output ? (
              command.startsWith('/sh ') ? (
                <AnsiBlock text={output} />
              ) : (
                <DeferredMarkdown text={output} cls="msg-agent" />
              )
            ) : null}
          </div>
        )}

        {/* Tool cards inline */}
        {toolCards && toolCards.length > 0 && (
          <div className="terminal-block__tools">
            {toolCards.map((card) => (
              <ToolCard key={card.id} tool={card} theme={theme} />
            ))}
          </div>
        )}

        {/* Thinking blocks inline */}
        {thinkingBlocks && thinkingBlocks.length > 0 && (
          <div className="terminal-block__thinking">
            {thinkingBlocks.map((text, i) => (
              <div key={i} className="terminal-block__think-chunk">{text}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
});

export default TerminalBlock;
