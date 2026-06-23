import { useMemo } from 'react';

interface SearchResultEntry {
  key: number;
  raw?: string;
  kind?: string;
  symbol?: string;
  file?: string;
  lineno?: number;
  content?: string;
}

interface SearchResultsProps {
  content: string;
}

const CONTAINER_STYLE: React.CSSProperties = {
  padding: '6px 0',
  margin: '4px 0',
  maxWidth: '100%',
  fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
  fontSize: '0.82em',
  lineHeight: '1.65',
};

export default function SearchResults({ content }: SearchResultsProps) {
  const lines: SearchResultEntry[] = useMemo(() => {
    if (!content) return [];
    return content.split('\n').map((line, i) => ({
      key: i,
      ...parseLine(line),
    }));
  }, [content]);

  if (lines.length === 0) return null;

  return (
    <div style={CONTAINER_STYLE}>
      {lines.map((entry) => {
        if (entry.raw !== undefined) {
          return (
            <div key={entry.key} style={{ color: '#e0e0e0', padding: '1px 0' }}>
              {entry.raw}
            </div>
          );
        }
        if (entry.kind) {
          return (
            <div key={entry.key} style={{ padding: '1px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' } as React.CSSProperties}>
              <span style={{ color: '#ce9d7c' }}>{entry.kind}</span>
              {' '}
              <span style={{ color: '#dcdcaa' }}>{entry.symbol}</span>
              {' -> '}
              <span style={{ color: '#6cc8e8' }}>{entry.file}</span>
              {':'}
              <span style={{ color: '#b8d975', userSelect: 'none' } as React.CSSProperties}>{entry.lineno}</span>
            </div>
          );
        }
        return (
          <div key={entry.key} style={{ padding: '1px 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word' } as React.CSSProperties}>
            <span style={{ color: '#6cc8e8' }}>{entry.file}</span>
            {':'}
            <span style={{ color: '#b8d975', userSelect: 'none' } as React.CSSProperties}>{entry.lineno}</span>
            {': '}
            <span style={{ color: '#e0e0e0' }}>{entry.content}</span>
          </div>
        );
      })}
    </div>
  );
}

// -- parser ------------------------------------------------------------------

const SEARCH_LINE_RE = /^(.+):(\d+): ?(.*)$/;
const SYMBOL_LINE_RE = /^\s*(\S+)\s+(\S+)\s+->\s+(.+):(\d+)$/;

function parseLine(line: string): Omit<SearchResultEntry, 'key'> {
  let m = line.match(SEARCH_LINE_RE);
  if (m) {
    return { file: m[1], lineno: parseInt(m[2], 10), content: m[3] };
  }
  m = line.match(SYMBOL_LINE_RE);
  if (m) {
    return {
      kind: m[1],
      symbol: m[2],
      file: m[3],
      lineno: parseInt(m[4], 10),
    };
  }
  return { raw: line };
}
