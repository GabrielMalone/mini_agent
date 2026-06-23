import { useMemo } from 'react';

interface ParsedSearchEntry {
  key: number;
  raw?: string;
  file?: string;
  lineno?: number;
  content?: string;
  kind?: string;
  symbol?: string;
}

const SEARCH_LINE_RE = /^(.+):(\d+): ?(.*)$/;
const SYMBOL_LINE_RE = /^\s*(\S+)\s+(\S+)\s+->\s+(.+):(\d+)$/;

function parseLine(line: string): ParsedSearchEntry {
  let m = line.match(SEARCH_LINE_RE);
  if (m) {
    return { key: 0, file: m[1], lineno: parseInt(m[2], 10), content: m[3] };
  }
  m = line.match(SYMBOL_LINE_RE);
  if (m) {
    return {
      key: 0,
      kind: m[1],
      symbol: m[2],
      file: m[3],
      lineno: parseInt(m[4], 10),
    };
  }
  return { key: 0, raw: line };
}

const CONTAINER_STYLE: React.CSSProperties = {
  padding: '6px 0',
  margin: '4px 0',
  maxWidth: '100%',
  fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", monospace',
  fontSize: '0.82em',
  lineHeight: '1.65',
};

const ROW_STYLE: React.CSSProperties = {
  padding: '1px 0',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
} as const;

const FILE_STYLE: React.CSSProperties = { color: '#6cc8e8' };
const LINENO_SPAN: React.CSSProperties = { color: '#b8d975', userSelect: 'none' };
const CONTENT_STYLE: React.CSSProperties = { color: '#e0e0e0' };
const KIND_STYLE: React.CSSProperties = { color: '#ce9d7c' };
const SYMBOL_STYLE: React.CSSProperties = { color: '#dcdcaa' };
const RAW_STYLE: React.CSSProperties = { color: '#e0e0e0', padding: '1px 0' };

interface SearchResultsProps {
  content: string;
}

export default function SearchResults({ content }: SearchResultsProps) {
  const lines = useMemo(() => {
    if (!content) return [] as ParsedSearchEntry[];
    return content.split('\n').map((line, i) => {
      const { key: _key, ...rest } = parseLine(line);
      return { key: i, ...rest };
    });
  }, [content]);

  if (lines.length === 0) return null;

  return (
    <div style={CONTAINER_STYLE}>
      {lines.map((entry) => {
        if (entry.raw !== undefined) {
          return (
            <div key={entry.key} style={RAW_STYLE}>
              {entry.raw}
            </div>
          );
        }
        if (entry.kind) {
          return (
            <div key={entry.key} style={ROW_STYLE}>
              <span style={KIND_STYLE}>{entry.kind}</span>
              {' '}
              <span style={SYMBOL_STYLE}>{entry.symbol}</span>
              {' -> '}
              <span style={FILE_STYLE}>{entry.file}</span>
              {':'}
              <span style={LINENO_SPAN}>{entry.lineno}</span>
            </div>
          );
        }
        return (
          <div key={entry.key} style={ROW_STYLE}>
            <span style={FILE_STYLE}>{entry.file}</span>
            {':'}
            <span style={LINENO_SPAN}>{entry.lineno}</span>
            {': '}
            <span style={CONTENT_STYLE}>{entry.content}</span>
          </div>
        );
      })}
    </div>
  );
}
