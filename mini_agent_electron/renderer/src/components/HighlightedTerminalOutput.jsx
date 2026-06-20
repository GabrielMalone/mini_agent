import { useState, useEffect, useRef } from 'react';
import { getHighlighter } from './CodeBlock';
import AnsiBlock from './AnsiBlock';

// ---------------------------------------------------------------------------
// HighlightedTerminalOutput — Shiki syntax-highlighted terminal output
//
// Takes raw ANSI terminal output and a command string.  Strips ANSI codes,
// guesses a language from the command args, runs Shiki highlighting, and
// falls back to plain AnsiBlock when no language is detected or Shiki fails.
// ---------------------------------------------------------------------------

// -- strip ANSI escape sequences --------------------------------------------

function stripAnsi(text) {
  if (!text) return '';
  // Remove ESC[...m and OSC sequences, but keep the underlying content
  return text
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/\x1b\].*?(\x1b\\|\x07)/g, '')
    .replace(/\x1b[PX^_].*?(\x1b\\|\x07)/g, '');
}

// -- language detection from /sh command ------------------------------------

const EXT_TO_LANG = {
  py: 'python', js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
  rs: 'rust', go: 'go', java: 'java', rb: 'ruby', php: 'php', swift: 'swift',
  kt: 'kotlin', scala: 'scala', hs: 'haskell', ml: 'ocaml', nim: 'nim', zig: 'zig',
  r: 'r', pl: 'perl', jl: 'julia', lua: 'lua', ex: 'elixir', dart: 'dart',
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp',
  json: 'json', yaml: 'yaml', yml: 'yaml', toml: 'toml', xml: 'xml', svg: 'xml',
  ini: 'ini', cfg: 'ini', conf: 'ini', env: 'dotenv',
  html: 'html', css: 'css', scss: 'scss', less: 'less',
  md: 'markdown', markdown: 'markdown',
  sh: 'shellscript', bash: 'shellscript', zsh: 'shellscript',
  dockerfile: 'docker', makefile: 'make',
  sql: 'sql',
  diff: 'diff', patch: 'diff',
  graphql: 'gql', gql: 'gql',
  vue: 'vue', svelte: 'svelte',
  csv: 'csv', tsv: 'csv',
};

const CMD_LANG_MAP = {
  'git diff': 'diff',
  'git show': 'diff',
  'git log': 'git-commit',
  'docker run': 'docker',
  'docker build': 'docker',
};

function guessLangFromCommand(command) {
  if (!command) return null;

  // Strip leading /sh if present
  const cmd = command.replace(/^\/sh\s+/, '').trim();

  // Check command-pattern matches first (stronger signal)
  for (const [pattern, lang] of Object.entries(CMD_LANG_MAP)) {
    if (cmd.startsWith(pattern)) return lang;
  }

  // Extract file paths from command args
  const args = cmd.split(/\s+/).slice(1); // skip the command itself
  for (const arg of args) {
    // Strip quotes and leading dashes (options)
    const clean = arg.replace(/^['"]|['"]$/g, '');
    if (clean.startsWith('-')) continue;

    // Check for extension
    const dotIdx = clean.lastIndexOf('.');
    if (dotIdx !== -1) {
      const ext = clean.slice(dotIdx + 1).toLowerCase();
      if (EXT_TO_LANG[ext]) return EXT_TO_LANG[ext];
    }

    // Check for well-known filenames
    const basename = clean.split('/').pop().toLowerCase();
    if (EXT_TO_LANG[basename]) return EXT_TO_LANG[basename];
  }

  return null;
}

// -- Shiki highlight wrapper ------------------------------------------------

function stripBg(html) {
  return html.replace(/(<pre[^>]*style=")background-color:#[0-9a-fA-F]+;?/g, '$1');
}

function PlainFallback({ text }) {
  return <AnsiBlock text={text} />;
}

export default function HighlightedTerminalOutput({ text, command }) {
  const [html, setHtml] = useState(null);
  const [failed, setFailed] = useState(false);
  const mountedRef = useRef(true);

  const lang = guessLangFromCommand(command);
  const clean = stripAnsi(text);

  useEffect(() => {
    mountedRef.current = true;
    let cancelled = false;

    if (!lang || !clean) {
      setFailed(true);
      return;
    }

    getHighlighter()
      .then((h) => {
        if (cancelled || !mountedRef.current) return;
        try {
          const htmlStr = h.codeToHtml(clean, { lang, theme: 'dark-plus' });
          if (!cancelled && mountedRef.current) setHtml(stripBg(htmlStr));
        } catch {
          if (!cancelled && mountedRef.current) setFailed(true);
        }
      })
      .catch(() => {
        if (!cancelled && mountedRef.current) setFailed(true);
      });

    return () => { cancelled = true; };
  }, [text, command, lang, clean]);

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  // No language detected or Shiki failed — fall back to AnsiBlock for ANSI colors
  if (!lang || failed) {
    return <PlainFallback text={text} />;
  }

  // Shiki still loading — show AnsiBlock as placeholder
  if (!html) {
    return <PlainFallback text={text} />;
  }

  return (
    <div
      className="shiki-wrapper terminal-shiki"
      style={{ background: 'transparent' }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
