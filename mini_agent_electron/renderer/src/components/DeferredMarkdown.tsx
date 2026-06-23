import { useState, useEffect, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface DeferredMarkdownProps {
  text: string;
  markdown?: boolean;
  cls?: string;
}

const DeferredMarkdown = memo(function DeferredMarkdown({ text, markdown = true, cls = '' }: DeferredMarkdownProps) {
  const [parsed, setParsed] = useState<string | null>(null);

  useEffect(() => {
    if (!markdown) return;
    const id = requestAnimationFrame(() => setParsed(text));
    return () => cancelAnimationFrame(id);
  }, [text, markdown]);

  if (!text || !text.trim()) return null;

  if (!markdown) {
    return (
      <pre className={cls} style={{ whiteSpace: 'pre-wrap', margin: 0, fontFamily: 'inherit', fontSize: 'inherit' }}>
        {text}
      </pre>
    );
  }

  if (!parsed) {
    return (
      <pre className={cls} style={{ whiteSpace: 'pre-wrap', margin: 0, fontFamily: 'inherit', fontSize: 'inherit' }}>
        {text}
      </pre>
    );
  }

  return (
    <div className={cls}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {parsed}
      </ReactMarkdown>
    </div>
  );
});

export default DeferredMarkdown;
