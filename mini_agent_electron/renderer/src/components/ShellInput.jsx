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
//   value          - controlled text value
//   onChange(txt)  - called when text changes
//   onSubmit(txt)  - called when user presses Enter (not Shift+Enter)
//   disabled       - when true, editor is read-only
//   placeholder    - placeholder text
//   ghostText      - dimmed ghost text shown when input is empty (last command)
//   commandHistory - array of past commands for Up/Down navigation
//   autoFocus      - focus on mount
// ---------------------------------------------------------------------------
const ShellInput = forwardRef(function ShellInput({
  value = '',
  onChange,
  onSubmit,
  disabled = false,
  placeholder = 'Type a message, /command, or drop files here...',
  ghostText = '',
  commandHistory = [],
  autoFocus = true,
}, ref) {
  const containerRef = useRef(null);
  const viewRef = useRef(null);
  const editableCompartment = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  const onSubmitRef = useRef(onSubmit);
  const disabledRef = useRef(disabled);
  const ghostTextRef = useRef(ghostText);
  const historyRef = useRef(commandHistory);
  const historyIdxRef = useRef(-1);       // -1 = not navigating history
  const savedInputRef = useRef('');        // saved input before history nav
  onChangeRef.current = onChange;
  onSubmitRef.current = onSubmit;
  disabledRef.current = disabled;
  ghostTextRef.current = ghostText;
  historyRef.current = commandHistory;

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
          // Reset history nav on submit
          historyIdxRef.current = -1;
          savedInputRef.current = '';
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
    // Escape blurs the editor (and resets history nav)
    {
      key: 'Escape',
      run: (view) => {
        historyIdxRef.current = -1;
        savedInputRef.current = '';
        view.contentDOM.blur();
        return true;
      },
    },
    // Up arrow -- navigate command history backward (older)
    {
      key: 'ArrowUp',
      run: (view) => {
        if (disabledRef.current) return false;
        const history = historyRef.current;
        if (history.length === 0) return false;

        // If cursor is not on the first line, let CodeMirror handle it
        const cursor = view.state.selection.main.head;
        const doc = view.state.doc;
        if (doc.lineAt(cursor).number > 1) return false;

        if (historyIdxRef.current === -1) {
          // Starting history navigation -- save current input
          savedInputRef.current = doc.toString();
          historyIdxRef.current = 0;
        } else if (historyIdxRef.current < history.length - 1) {
          historyIdxRef.current++;
        }
        // else: at oldest entry, stay there

        const entry = history[historyIdxRef.current] || '';
        view.dispatch({
          changes: { from: 0, to: doc.length, insert: entry },
          selection: { anchor: entry.length },
        });
        return true;
      },
    },
    // Down arrow -- navigate command history forward (newer)
    {
      key: 'ArrowDown',
      run: (view) => {
        if (disabledRef.current) return false;
        if (historyIdxRef.current === -1) return false;

        // If cursor is not on the last line, let CodeMirror handle it
        const cursor = view.state.selection.main.head;
        const doc = view.state.doc;
        if (doc.lineAt(cursor).number < doc.lines) return false;

        historyIdxRef.current--;

        if (historyIdxRef.current < 0) {
          // Back to original input
          historyIdxRef.current = -1;
          const restored = savedInputRef.current;
          savedInputRef.current = '';
          view.dispatch({
            changes: { from: 0, to: doc.length, insert: restored },
            selection: { anchor: restored.length },
          });
        } else {
          const entry = history[historyIdxRef.current] || '';
          view.dispatch({
            changes: { from: 0, to: doc.length, insert: entry },
            selection: { anchor: entry.length },
          });
        }
        return true;
      },
    },
    // Tab or Right arrow when empty -- fill ghost text
    {
      key: 'Tab',
      run: (view) => {
        if (disabledRef.current) return false;
        const doc = view.state.doc.toString();
        if (doc === '' && ghostTextRef.current) {
          view.dispatch({
            changes: { from: 0, to: 0, insert: ghostTextRef.current },
            selection: { anchor: ghostTextRef.current.length },
          });
          return true;
        }
        return false; // let other handlers (autocomplete) work
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
    <div className="shell-input-wrapper">
      <div
        ref={containerRef}
        className="shell-input-cm"
      />
      {/* Ghost text overlay -- shown when input is empty */}
      {!value && ghostText && !disabled && (
        <div className="shell-input-ghost" aria-hidden="true">
          <span className="shell-input-ghost__text">{ghostText}</span>
          <span className="shell-input-ghost__hint">(tab)</span>
        </div>
      )}
    </div>
  );
});

export default ShellInput;
