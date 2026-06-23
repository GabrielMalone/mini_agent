import { memo } from 'react';

interface CharStreamProps {
  text: string;
  className?: string;
}

const CharStream = memo(function CharStream({ text, className = '' }: CharStreamProps) {
  return <span className={className}>{text}</span>;
});

export default CharStream;
