import { useState, useEffect, useRef } from 'react';
import { getHighlighter } from './CodeBlock';
import AnsiBlock from './AnsiBlock';

// ---------------------------------------------------------------------------
// HighlightedTerminalOutput — syntax-highlighted terminal output
//
// 1. ls -la / ll output → custom per-file-type coloring (dirs blue, exe green,
//    symlinks cyan, devices yellow …)
// 2. Shiki-detected language (from command args) → full syntax highlight
// 3. Fallback → AnsiBlock (plain ANSI rendering)
// ---------------------------------------------------------------------------

// -- strip ANSI escape sequences --------------------------------------------

function stripAnsi(text) {
  if (!text) return '';
  return text
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')
    .replace(/\x1b\].*?(\x1b\\|\x07)/g, '')
    .replace(/\x1b[PX^_].*?(\x1b\\|\x07)/g, '');
}

// -- ls -la output detection & highlighting ----------------------------------

/**
 * Match a single line of `ls -l` output, e.g.
 *   drwxr-xr-x  12 user  staff   384 Jan 15 10:30 src
 *   -rw-r--r--   1 user  staff  1234 Jan 15 10:28 README.md
 *   lrwxr-xr-x   1 user  staff    10 Jan 15 10:29 link -> target
 *
 * Groups:
 *   [1] file-type char  (d, -, l, c, b, p, s)
 *   [2] permissions     (rwxr-xr-x etc.)
 *   [3] rest of line    (link count, owner, group, size, date, name …)
 */
const LS_LINE_RE = /^([d\-lcbps])([r\-wxsStT]{9})(\s+.*)$/;

function looksLikeLsOutput(text) {
  if (!text) return false;
  const lines = text.split('\n').filter(Boolean);
  if (lines.length === 0) return false;

  // Skip a leading "total N" line
  let matchable = 0;
  let matched = 0;
  for (const line of lines) {
    if (/^total\s+\d+/i.test(line.trim())) continue;
    matchable++;
    if (LS_LINE_RE.test(line)) matched++;
  }
  // At least half of non-total lines should look like `ls -l` rows
  return matchable > 0 && matched / matchable >= 0.5;
}

// -- color helpers -----------------------------------------------------------

const CSS = {
  // permissions and metadata columns — muted
  meta:   'color:#6a737d',
  // file-type colors (mimic `ls --color=auto`)
  dir:    'color:#569CD6;font-weight:bold',   // blue – directories
  exe:    'color:#6A9955;font-weight:bold',   // green – executables (x bit set)
  sym:    'color:#4EC9B0;font-weight:bold',   // cyan – symlinks
  dev:    'color:#CE9178;font-weight:bold',   // orange – device files
  broken: 'color:#F44747',                     // red – broken symlinks
  normal: 'color:#D4D4D4',                    // default text
};

function colorizeLsLine(line) {
  const m = line.match(LS_LINE_RE);
  if (!m) {
    // Non-matching line (e.g. "total 48") — render as-is, muted
    return `<span style="${CSS.meta}">${esc(line)}</span>`;
  }

  const typeChar = m[1];
  const perms    = m[2];
  const rest     = m[3]; // includes leading spaces

  // Parse the rest to separate metadata from filename
  // Format: sp linkCount sp owner sp group sp size sp month sp day sp time/year sp name...
  const restTrimmed = rest.trim();
  const tokens = restTrimmed.split(/\s+/);

  // We need at least: linkCount owner group size month day time name
  if (tokens.length < 8) {
    return `<span style="${CSS.normal}">${esc(line)}</span>`;
  }

  // The filename starts at token index 7 (0-based), but may include spaces
  // if the filename itself contains spaces (rare in ls output but possible).
  // Simpler: find where the date columns end.
  // Columns: 0=links, 1=owner, 2=group, 3=size, 4=month, 5=day, 6=time/year
  // Token 7+ = filename (may contain spaces if quoted, but ls doesn't quote)
  const metaTokens = tokens.slice(0, 7); // links, owner, group, size, month, day, time
  const nameTokens = tokens.slice(7);
  let name = nameTokens.join(' ');

  // Check for symlink arrow "-> target"
  let linkTarget = '';
  const arrowIdx = name.indexOf(' -> ');
  if (arrowIdx !== -1) {
    linkTarget = name.slice(arrowIdx + 4);
    name = name.slice(0, arrowIdx);
  }

  // Determine file-type style
  let nameStyle = CSS.normal;
  if (typeChar === 'd') {
    nameStyle = CSS.dir;
  } else if (typeChar === 'l') {
    nameStyle = CSS.sym;
  } else if (typeChar === 'c' || typeChar === 'b') {
    nameStyle = CSS.dev;
  } else if (/[xX]/.test(perms)) {
    // Executable bit set (user, group, or other)
    nameStyle = CSS.exe;
  }

  // Build the HTML
  let html = '';
  // Type char + permissions
  html += `<span style="${CSS.meta}">${esc(typeChar)}</span>`;
  html += `<span style="${CSS.meta}">${esc(perms)}</span>`;

  // Metadata columns (links, owner, group, size, date)
  // Rebuild the spacing from the original `rest` string before the filename
  const beforeName = rest.slice(0, rest.lastIndexOf(nameTokens[0]));
  html += `<span style="${CSS.meta}">${esc(beforeName)}</span>`;

  // Filename
  if (typeChar === 'l' && linkTarget) {
    html += `<span style="${nameStyle}">${esc(name)}</span>`;
    html += `<span style="${CSS.meta}"> -&gt; </span>`;
    html += `<span style="${nameStyle}">${esc(linkTarget)}</span>`;
  } else {
    html += `<span style="${nameStyle}">${esc(name)}</span>`;
  }

  return html;
}

function highlightLsOutput(text) {
  const lines = text.split('\n');
  return lines.map((line, i) => {
    const trimmed = line.trimEnd();
    if (!trimmed) return ''; // preserve blank lines
    return colorizeLsLine(trimmed);
  }).filter(h => h !== '').join('\n');
}

function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// -- is it an ls command? ----------------------------------------------------

function isLsCommand(command) {
  if (!command) return false;
  const cmd = command.replace(/^\/sh\s+/, '').trim();
  return /^(ls|ll|dir|vdir)(\s|$)/.test(cmd);
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
  const cmd = command.replace(/^\/sh\s+/, '').trim();

  for (const [pattern, lang] of Object.entries(CMD_LANG_MAP)) {
    if (cmd.startsWith(pattern)) return lang;
  }

  const args = cmd.split(/\s+/).slice(1);
  for (const arg of args) {
    const clean = arg.replace(/^['"]|['"]$/g, '');
    if (clean.startsWith('-')) continue;

    const dotIdx = clean.lastIndexOf('.');
    if (dotIdx !== -1) {
      const ext = clean.slice(dotIdx + 1).toLowerCase();
      if (EXT_TO_LANG[ext]) return EXT_TO_LANG[ext];
    }

    const basename = clean.split('/').pop().toLowerCase();
    if (EXT_TO_LANG[basename]) return EXT_TO_LANG[basename];
  }

  return null;
}

// -- Shiki wrapper -----------------------------------------------------------

function stripBg(html) {
  return html.replace(/(<pre[^>]*style=")background-color:#[0-9a-fA-F]+;?/g, '$1');
}

// -- component ---------------------------------------------------------------

function LsHighlightedOutput({ text }) {
  const clean = stripAnsi(text);
  const html = highlightLsOutput(clean);

  return (
    <pre
      className="terminal-ls-output"
      style={{
        margin: 0,
        padding: '6px 10px',
        fontFamily: 'var(--font-family)',
        fontSize: '0.82em',
        lineHeight: 1.5,
        background: 'transparent',
        color: '#D4D4D4',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflowX: 'auto',
      }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function ShikiHighlightedOutput({ text, command }) {
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

  if (!lang || failed || !html) {
    return <AnsiBlock text={text} />;
  }

  return (
    <div
      className="shiki-wrapper terminal-shiki"
      style={{ background: 'transparent' }}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

export default function HighlightedTerminalOutput({ text, command }) {
  // 1. ls commands → custom per-file-type coloring
  if (isLsCommand(command) && looksLikeLsOutput(text)) {
    return <LsHighlightedOutput text={text} />;
  }

  // 2. Shiki-detected language
  const lang = guessLangFromCommand(command);
  if (lang) {
    return <ShikiHighlightedOutput text={text} command={command} />;
  }

  // 3. Also try ls detection on output alone (command might be something like
  //    /sh run.sh that happens to output a file listing)
  if (looksLikeLsOutput(text)) {
    return <LsHighlightedOutput text={text} />;
  }

  // 4. Fallback to ANSI
  return <AnsiBlock text={text} />;
}
