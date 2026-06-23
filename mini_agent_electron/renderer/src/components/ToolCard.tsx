import { useState, useEffect, useRef, memo, useCallback } from 'react';
import CodeBlock from './CodeBlock';
import SearchResults from './SearchResults';
import ReadFileResult from './ReadFileResult';
import ShellResults from './ShellResults';
import AstResult from './AstResult';
import type { ToolCardData } from '../types';

const AUTO_COLLAPSE_MS = 3000;

interface ToolCardProps {
  tool: ToolCardData;
  theme?: string;
}

const ToolCard = memo(function ToolCard({ tool, theme }: ToolCardProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [manuallyToggled, setManuallyToggled] = useState(false);
  const bodyRef = useRef<HTMLDivElement | null>(null);

  const isRunning = tool.status === 'running';
  const isOk = tool.status === 'ok';
  const isErr = tool.status === 'err';

  useEffect(() => {
    if (!isOk || manuallyToggled) return;
    const timer = setTimeout(() => setCollapsed(true), AUTO_COLLAPSE_MS);
    return () => clearTimeout(timer);
  }, [isOk, manuallyToggled, tool.id]);

  useEffect(() => {
    if (isErr) setCollapsed(false);
  }, [isErr, tool.id]);

  const toggle = useCallback(() => {
    setCollapsed((c) => !c);
    setManuallyToggled(true);
  }, []);

  const hasOutput = tool.output && tool.output.trim().length > 0;
  const hasDiff = tool.diffPreview && tool.diffPreview.trim().length > 0;
  const isSingleLine = hasOutput && !tool.output.includes('\n');

  const duration = tool.endTime && tool.startTime
    ? `${((tool.endTime - tool.startTime) / 1000).toFixed(1)}s`
    : null;

  return (
    <div className={`tool-card tool-card-${tool.status}`}>
      <div className="tool-card-header" onClick={toggle}>
        <span className="tool-card-status">
          {isRunning && <span className="tool-card-spinner" />}
          {isOk && <span className="tool-card-check">{'\u2713'}</span>}
          {isErr && <span className="tool-card-x">{'\u2717'}</span>}
        </span>
        <span className="tool-card-name">{tool.toolName}</span>
        {tool.toolArgs && (
          <span className="tool-card-args dim">{tool.toolArgs}</span>
        )}
        <span className="tool-card-duration">
          {isRunning && <span className="tool-card-running-label">running</span>}
          {duration && <span className="dim">{duration}</span>}
        </span>
        <span className={`tool-card-chevron ${collapsed ? '' : 'expanded'}`}>
          {'\u25B6'}
        </span>
      </div>

      {!collapsed && (hasOutput || isRunning) && (
        <div className="tool-card-body" ref={bodyRef}>
          {isRunning && !hasOutput && (
            <div className="tool-card-waiting dim">waiting for output...</div>
          )}
          {hasOutput && isSingleLine && (
            <div className="tool-card-output-single dim">{tool.output}</div>
          )}
          {hasOutput && !isSingleLine && (
            <ToolOutput
              output={tool.output}
              toolName={tool.toolName}
              theme={theme}
            />
          )}
        </div>
      )}

      {!collapsed && hasDiff && (
        <div className="tool-card-diff">
          <CodeBlock
            code={tool.diffPreview}
            language="diff"
            fontSize="0.68em"
            theme={theme}
          />
        </div>
      )}

      {!collapsed && isErr && tool.errorDetail && (
        <div className="tool-card-error">{tool.errorDetail}</div>
      )}
    </div>
  );
});

interface ToolOutputProps {
  output: string;
  toolName: string;
  theme?: string;
}

function ToolOutput({ output, toolName, theme }: ToolOutputProps) {
  const name = toolName || '';

  if (/^search_files\(|^find_symbol\(|^find_usages\(|^semantic_search\(|^web_search\(/.test(name)) {
    return <SearchResults content={output} />;
  }
  if (/^read_file\(/.test(name)) {
    return <ReadFileResult content={output} toolName={name} />;
  }
  if (/^run_shell\(|^run_tests\(/.test(name)) {
    return <ShellResults content={output} ok={true} />;
  }
  if (/^get_file_skeleton\(|^get_function\(/.test(name)) {
    return <AstResult content={output} toolName={name} />;
  }

  return <CodeBlock code={output} fontSize="0.72em" toolName={name} theme={theme} wrap={true} />;
}

export default ToolCard;
