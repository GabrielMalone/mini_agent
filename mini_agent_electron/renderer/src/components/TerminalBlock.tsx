import { memo, type ReactNode } from 'react';
import DeferredMarkdown from './DeferredMarkdown';
import HighlightedTerminalOutput from './HighlightedTerminalOutput';
import type { ChatBlock } from '../types';

interface TerminalBlockProps {
  block: ChatBlock;
  streamingOutput?: string;
  isRunning?: boolean;
  onEdit?: (cmd: string) => void;
  theme?: string;
}

const STATUS_COLORS: Record<string, string> = {
  ok: 'var(--green)',
  err: 'var(--red)',
  running: 'var(--pulse)',
};

function formatElapsed(ms: number): string {
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
}: TerminalBlockProps) {
  const { id, command, output, status, timestamp } = block;
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
      <div className="terminal-block__notch" style={{ borderColor: notchColor }} />
      <div className="terminal-block__body">
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

        {(displayOutput || isRunning) && (
          <div className="terminal-block__output">
            {isRunning ? (
              <div className="terminal-block__spinner">
                <span className="think-pulse" />
                <span className="think-label">thinking</span>
              </div>
            ) : output ? (
              command.startsWith('/sh ') ? (
                <HighlightedTerminalOutput text={output} command={command} />
              ) : (
                <DeferredMarkdown text={output} cls="msg-agent" />
              )
            ) : null}
          </div>
        )}


      </div>
    </div>
  );
});

export default TerminalBlock;
