import { useEffect, useRef, useCallback, useMemo, forwardRef, useImperativeHandle } from 'react';
import { EditorView, keymap, placeholder as placeholderExt } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { StreamLanguage } from '@codemirror/language';
import { shell } from '@codemirror/legacy-modes/mode/shell';
import { autocompletion } from '@codemirror/autocomplete';

// ---------------------------------------------------------------------------
// Dynamic theme -- reads CSS custom properties at render time so the editor
// tracks theme switches without a remount.
// ---------------------------------------------------------------------------
function readThemeVars() {
  const style = getComputedStyle(document.documentElement);
  return {
    bg: style.getPropertyValue('--bg').trim() || '#000000',
    text: style.getPropertyValue('--text').trim() || '#c8c8c8',
    accent: style.getPropertyValue('--accent').trim() || '#e0e0e0',
    dim: style.getPropertyValue('--dim').trim() || '#666666',
    green: style.getPropertyValue('--green').trim() || '#8cc265',
    yellow: style.getPropertyValue('--yellow').trim() || '#999999',
    red: style.getPropertyValue('--red').trim() || '#e05555',
    fontFamily: style.getPropertyValue('--font-family').trim(),
    fontSize: style.getPropertyValue('--font-size').trim() || '13px',
  };
}

function shellTheme() {
  const v = readThemeVars();
  return EditorView.theme({
    '&': {
      flex: '1',
      fontSize: v.fontSize,
      fontFamily: v.fontFamily,
      lineHeight: '1.5',
      color: v.text,
      background: 'transparent',
      caretColor: v.accent,
    },
    '.cm-content': {
      fontFamily: `${v.fontFamily} !important`,
      padding: '0 !important',
      caretColor: v.accent,
    },
    '.cm-line': {
      padding: '0 !important',
    },
    '.cm-cursor, .cm-dropCursor': {
      borderLeftColor: v.accent,
    },
    '&.cm-focused .cm-cursor': {
      borderLeftColor: v.accent,
    },
    '.cm-selectionBackground, .cm-selectionMatch, &.cm-focused .cm-selectionBackground, ::selection': {
      backgroundColor: `${v.accent}33 !important`,
    },
    '.cm-activeLine': {
      backgroundColor: 'transparent !important',
    },
    '.cm-gutters': {
      display: 'none',
    },
    '.cm-placeholder': {
      color: v.dim,
    },
    // Syntax tokens -- shell mode
    '.cmt-keyword':    { color: v.accent, fontWeight: '600' },
    '.cmt-builtin':    { color: v.accent },
    '.cmt-number':     { color: v.green },
    '.cmt-string':     { color: v.green },
    '.cmt-meta':       { color: v.yellow },
    '.cmt-comment':    { color: v.dim, fontStyle: 'italic' },
    '.cmt-variableName': { color: v.text },
    '.cmt-typeName':   { color: v.yellow },
    '.cmt-operator':   { color: v.accent },
    '.cmt-punctuation': { color: v.dim },
    '.cmt-bracket':    { color: v.dim },
    '.cmt-link':       { color: v.accent, textDecoration: 'underline' },
    // Error underline (invalid commands)
    '.cmt-invalid':    { color: v.red },
  }, { dark: true });
}

// ---------------------------------------------------------------------------
// Autocomplete -- basic /command completer (extensible)
// ---------------------------------------------------------------------------
const commands = [
  { label: '/workspace', detail: 'Switch workspace directory' },
  { label: '/theme', detail: 'Switch color theme' },
  { label: '/session', detail: 'List or switch sessions' },
  { label: '/clear', detail: 'Clear chat history' },
  { label: '/cancel', detail: 'Cancel current turn' },
  { label: '/help', detail: 'Show available commands' },
];

function shellCompletions(context) {
  const word = context.matchBefore(/\/\w*/);
  if (!word || (word.from === word.to && !context.explicit)) return null;
  return {
    from: word.from,
    options: commands.filter((c) => c.label.startsWith(word.text)),
  };
}

// ---------------------------------------------------------------------------
// ShellInput -- CodeMirror-backed shell input for mini_agent
//
// Props:
//   value         - controlled text value
//   onChange(txt) - called when text changes
//   onSubmit(txt) - called when user presses Enter (not Shift+Enter)
//   disabled      - when true, editor is read-only
//   placeholder   - placeholder text
//   autoFocus     - focus on mount
// ---------------------------------------------------------------------------
const ShellInput = forwardRef(function ShellInput({
  value = '',
  onChange,
  onSubmit,
  disabled = false,
  placeholder = 'Type a message, /command, or drop files here...',
  autoFocus = true,
}, ref) {
  const containerRef = useRef(null);
  const viewRef = useRef(null);
  const editableCompartment = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  const onSubmitRef = useRef(onSubmit);
  const disabledRef = useRef(disabled);
  onChangeRef.current = onChange;
  onSubmitRef.current = onSubmit;
  disabledRef.current = disabled;

  // Expose focus/blur to parent via ref
  useImperativeHandle(ref, () => ({
    focus: () => viewRef.current?.focus(),
    blur: () => viewRef.current?.contentDOM.blur(),
  }), []);

  const shellKeymap = useMemo(() => keymap.of([
    {
      key: 'Enter',
      run: (view) => {
        if (disabledRef.current) return false;
        const doc = view.state.doc.toString().trim();
        if (doc) {
          onSubmitRef.current?.(doc);
          view.dispatch({
            changes: { from: 0, to: view.state.doc.length, insert: '' },
          });
        }
        return true;
      },
    },
    {
      key: 'Shift-Enter',
      run: (view) => {
        if (disabledRef.current) return false;
        view.dispatch(view.state.replaceSelection('\n'));
        return true;
      },
    },
    // Escape blurs the editor
    {
      key: 'Escape',
      run: (view) => {
        view.contentDOM.blur();
        return true;
      },
    },
  ]), []);

  // --- create / reconcile editor -------------------------------------------
  useEffect(() => {
    if (!containerRef.current) return;

    // Build state
    const state = EditorState.create({
      doc: value,
      extensions: [
        // Minimal setup (no gutter, no line numbers, no fold)
        history(),
        EditorView.lineWrapping,
        editableCompartment.current.of(EditorView.editable.of(!disabled)),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) {
            const txt = update.state.doc.toString();
            onChangeRef.current?.(txt);
          }
        }),
        placeholderExt(placeholder),
        StreamLanguage.define(shell),
        shellKeymap,
        autocompletion({ override: [shellCompletions] }),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        shellTheme(),
        EditorState.transactionFilter.of((tr) => {
          if (disabledRef.current && tr.isUserEvent('input')) {
            return [];
          }
          return tr;
        }),
        EditorView.domEventHandlers({
          paste: (e) => {
            if (disabledRef.current) {
              e.preventDefault();
            }
          },
        }),
      ],
    });

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });

    viewRef.current = view;

    if (autoFocus && !disabled) {
      view.focus();
    }

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []); // mount only -- we reconcile value externally

  // --- sync external value changes into the editor -------------------------
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentDoc = view.state.doc.toString();
    if (value !== currentDoc) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      });
    }
  }, [value]);

  // --- sync disabled state -------------------------------------------------
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: editableCompartment.current.reconfigure(
        EditorView.editable.of(!disabled)
      ),
    });
  }, [disabled]);

  return (
    <div
      ref={containerRef}
      className="shell-input-cm"
    />
  );
});

export default ShellInput;
