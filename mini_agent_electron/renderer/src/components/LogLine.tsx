import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import CodeBlock from './CodeBlock';

interface LogLineData {
  component?: React.ReactNode;
  cls?: string;
  toolName?: string;
  toolArgs?: string;
  markdown?: boolean;
  text?: string;
}

interface LogLineProps {
  line: LogLineData;
}

const markdownComponents = {
  code({ className, children, inline, ...props }: { className?: string; children: React.ReactNode; inline?: boolean }) {
    const match = /language-(\w+)/.exec(className || '');
    const lang = match ? match[1] : undefined;
    const code = String(children).replace(/\n$/, '');
    return <CodeBlock code={code} language={lang} inline={inline} highlight={false} />;
  },
};

function escapeHtml(text: string): string {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const LogLine = memo(function LogLine({ line }: LogLineProps) {
  if (line.component) {
    return <div className={line.cls || ''}>{line.component}</div>;
  }

  if (line.toolName) {
    return (
      <div className={line.cls || ''}>
        <span className="accent">{line.toolName}</span>
        {line.toolArgs && <span className="dim">{line.toolArgs}</span>}
      </div>
    );
  }

  if (line.markdown) {
    return (
      <div className={`md-line ${line.cls || ''}`} style={{ whiteSpace: 'normal' }}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            p: ({ children }: { children: React.ReactNode }) => <span>{children}</span>,
            ...markdownComponents,
          }}
        >
          {line.text}
        </ReactMarkdown>
      </div>
    );
  }

  return <div className={line.cls || ''}>{escapeHtml(line.text || '')}</div>;
});

export default LogLine;
export type { LogLineData };
