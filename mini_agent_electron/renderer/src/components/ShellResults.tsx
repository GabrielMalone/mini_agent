import { useMemo } from 'react';
import AnsiBlock from './AnsiBlock';

interface ShellResultsProps {
  content: string;
  ok: boolean;
}

const CONTAINER_STYLE: React.CSSProperties = {
  padding: '6px 0',
  margin: '4px 0',
  maxWidth: '100%',
  fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
  fontSize: '0.82em',
  lineHeight: '1.65',
};

const STATUS_ROW_STYLE: React.CSSProperties = {
  display: 'flex',
  gap: '0.5em',
  alignItems: 'center',
  marginBottom: '4px',
};

const statusBadgeStyle = (ok: boolean): React.CSSProperties => ({
  display: 'inline-block',
  padding: '0px 7px',
  borderRadius: '3px',
  fontSize: '0.75em',
  fontWeight: 600,
  color: '#fff',
  background: ok ? '#3a7d44' : '#b04040',
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
});

const OUTPUT_LINE_STYLE: React.CSSProperties = {
  color: '#d4d4d4',
  padding: '1px 0',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

const DIM_LINE_STYLE: React.CSSProperties = {
  color: '#888',
  padding: '1px 0',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
};

const LINE_NO_SPAN: React.CSSProperties = {
  color: '#555',
  userSelect: 'none',
  display: 'inline-block',
  minWidth: '3em',
  textAlign: 'right',
  marginRight: '0.5em',
};

export default function ShellResults({ content, ok }: ShellResultsProps) {
  const lines = useMemo(() => {
    if (!content) return [] as string[];
    return content.split('\n');
  }, [content]);

  if (lines.length === 0) return null;

  return (
    <div style={CONTAINER_STYLE}>
      <div style={STATUS_ROW_STYLE}>
        <span style={statusBadgeStyle(ok)}>{ok ? 'OK' : 'ERR'}</span>
        <span style={{ color: '#999', fontSize: '0.78em' }}>
          exit={ok ? '0' : '\u22600'}
        </span>
      </div>
      {lines.map((line, i) => {
        const isDim = line === '' || /^-{3}$/.test(line.trim());
        const lineNum = String(i + 1).padStart(4, '\u00A0');
        return (
          <div key={i} style={isDim ? DIM_LINE_STYLE : OUTPUT_LINE_STYLE}>
            <span style={LINE_NO_SPAN}>{lineNum}  </span>
            {line ? <AnsiBlock text={line} /> : '\u00A0'}
          </div>
        );
      })}
    </div>
  );
}
