import { useMemo } from 'react';
import CodeBlock, { EXT_TO_LANG } from './CodeBlock';

interface AstResultProps {
  content: string;
  toolName: string;
}

interface ParsedAstResult {
  source: string;
  filePath: string | null;
  funcName: string | null;
  meta: { key: string; value: string } | null;
  lang: string | null;
  startLine: number;
  lineHashes: (string | null)[];
}

const HASHLINE_RE = /^\s*(\d+)(?:\s+(\w+)\u2502)?\s?/;
const HEADER_RE = /^[=-]+\s+(.+?)\s+[-]+$/;
const FUNC_LINE_RE = /^[=-]+\s+(.+?)\s+::\s+(.+?)\s+[-]+$/;
const META_RE = /^\[(Function Hash|Symbol|Symbol Hash):\s*(.+?)\]$/;
const COMPACTION_RE = /^\.\.\.\s*\[compacted\s+\d+\s*(?:chars|token)/;

function extToLang(filePath: string | null): string | null {
  if (!filePath) return null;
  const name = filePath.replace(/\\/g, '/').split('/').pop() || '';
  const lower = name.toLowerCase();
  if (EXT_TO_LANG[lower as keyof typeof EXT_TO_LANG]) return EXT_TO_LANG[lower as keyof typeof EXT_TO_LANG];
  const dotIdx = name.lastIndexOf('.');
  const ext = dotIdx >= 0 ? name.slice(dotIdx + 1).toLowerCase() : '';
  if (!ext && name.startsWith('.')) {
    if (EXT_TO_LANG[name as keyof typeof EXT_TO_LANG]) return EXT_TO_LANG[name as keyof typeof EXT_TO_LANG];
  }
  return EXT_TO_LANG[ext as keyof typeof EXT_TO_LANG] || null;
}

export default function AstResult({ content, toolName }: AstResultProps) {
  const parsed: ParsedAstResult = useMemo(() => {
    if (!content) return { source: '', filePath: null, funcName: null, meta: null, lang: null, startLine: 1, lineHashes: [] };

    const lines = content.split('\n');

    let headerIdx = 0;
    let filePath: string | null = null;
    let funcName: string | null = null;
    let meta: { key: string; value: string } | null = null;

    for (let i = 0; i < Math.min(lines.length, 3); i++) {
      const line = lines[i];
      const funcMatch = line.match(FUNC_LINE_RE);
      if (funcMatch) {
        filePath = funcMatch[1].trim();
        funcName = funcMatch[2].trim();
        headerIdx = i;
        break;
      }
      const headerMatch = line.match(HEADER_RE);
      if (headerMatch) {
        filePath = headerMatch[1].trim();
        headerIdx = i;
        break;
      }
      const metaMatch = line.match(META_RE);
      if (metaMatch) {
        meta = { key: metaMatch[1], value: metaMatch[2] };
        headerIdx = i;
        continue;
      }
      if (filePath || meta) break;
    }

    let metaScanIdx = headerIdx + 1;
    while (metaScanIdx < lines.length) {
      const line = lines[metaScanIdx];
      const metaMatch = line.match(META_RE);
      if (metaMatch) {
        meta = { key: metaMatch[1], value: metaMatch[2] };
        headerIdx = metaScanIdx;
        metaScanIdx++;
      } else if (line.trim() === '') {
        metaScanIdx++;
      } else {
        break;
      }
    }

    let codeStart = headerIdx + 1;
    while (codeStart < lines.length && lines[codeStart].trim() === '') {
      codeStart++;
    }

    let startLine = 0;
    const hashes: (string | null)[] = [];
    const strippedLines: string[] = [];

    for (let i = codeStart; i < lines.length; i++) {
      const line = lines[i];
      const m = line.match(HASHLINE_RE);
      if (m) {
        const lineNum = parseInt(m[1], 10);
        if (startLine === 0) startLine = lineNum;
        hashes.push(m[2] || null);
        strippedLines.push(line.replace(HASHLINE_RE, ''));
      } else if (COMPACTION_RE.test(line)) {
        hashes.push(null);
        strippedLines.push(line);
      } else {
        hashes.push(null);
        strippedLines.push(line);
      }
    }

    const source = strippedLines.join('\n');
    const lang = extToLang(filePath);

    return { source, filePath, funcName, meta, lang, startLine: startLine || 1, lineHashes: hashes };
  }, [content]);

  const { source, filePath, funcName, meta, lang, startLine, lineHashes } = parsed;

  if (!source.trim()) return null;

  const displayName = filePath
    ? filePath.replace(/\\/g, '/').split('/').pop()
    : null;

  return (
    <div style={{ margin: '4px 0' }}>
      {(filePath || funcName) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5em', padding: '3px 0 0', fontSize: '0.75em', color: '#888', fontFamily: '"JetBrains Mono", "Fira Code", monospace' } as React.CSSProperties}>
          <span>{'\uD83D\uDCC4'}</span>
          {displayName && (
            <span style={{ color: '#6cc8e8', fontWeight: 500 }}>{displayName}</span>
          )}
          {funcName && (
            <>
              <span style={{ color: '#555' }}>::</span>
              <span style={{ color: '#dcdcaa', fontWeight: 500 }}>{funcName}</span>
            </>
          )}
        </div>
      )}
      {meta && (
        <div style={{ fontSize: '0.72em', color: '#666', fontFamily: '"JetBrains Mono", "Fira Code", monospace', padding: '1px 0 2px' } as React.CSSProperties}>
          [{meta.key}: {meta.value}]
        </div>
      )}
      <CodeBlock
        code={source}
        fontSize="0.78em"
        language={lang}
        highlight={true}
        lineNumbers={true}
        startLine={startLine}
        lineHashes={lineHashes}
        wrap={true}
      />
    </div>
  );
}
