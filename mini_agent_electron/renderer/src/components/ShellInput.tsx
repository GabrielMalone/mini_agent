import { useEffect, useRef, useCallback, useMemo, useState, forwardRef, useImperativeHandle } from 'react';
import { EditorView, keymap, placeholder as placeholderExt, drawSelection, highlightSpecialChars, dropCursor } from '@codemirror/view';
import { EditorState, Compartment } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { StreamLanguage } from '@codemirror/language';
import { shell } from '@codemirror/legacy-modes/mode/shell';
import { autocompletion } from '@codemirror/autocomplete';
// NOTE: llmShellCompletions removed — LLM-based autocomplete returned incoherent
// suggestions. Static /command completion (shellCompletions) is sufficient.

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
      fontFamily: `"${v.fontFamily}", monospace`,
      backgroundColor: v.bg,
      color: v.text,
      caretColor: v.accent,
      outline: 'none',
      border: 'none',
    },
    '&.cm-focused': {
      outline: 'none',
    },
    '.cm-scroller': {
      outline: 'none',
    },
    '.cm-content': {
      fontFamily: `"${v.fontFamily}", monospace`,
      padding: '0 !important',
      outline: 'none',
    },
    '.cm-line': {
      padding: '0 !important',
    },
    '.cm-placeholder': {
      color: v.dim,
      fontStyle: 'italic',
      outline: 'none !important',
      border: 'none !important',
      boxShadow: 'none !important',
    },
    '.cm-selectionBackground': {
      backgroundColor: `${v.accent}33 !important`,
    },
    '.cm-completionIcon': {
      display: 'none',
    },
    '.cm-tooltip': {
      backgroundColor: v.bg,
      border: `1px solid ${v.dim}`,
      color: v.text,
      fontFamily: `"${v.fontFamily}", monospace`,
      fontSize: v.fontSize,
    },
    '.cm-tooltip-autocomplete': {
      '& > ul > li[aria-selected]': {
        backgroundColor: `${v.accent}33`,
        color: v.accent,
      },
    },
    '.cm-tooltip.cm-tooltip-autocomplete > ul > li': {
      padding: '2px 8px',
    },
  });
}

// ---------------------------------------------------------------------------
// Static command autocomplete
// ---------------------------------------------------------------------------
const commands = [
  { label: '/sh',        detail: 'Run a shell command' },
  { label: '/clear',     detail: 'Clear chat history' },
  { label: '/cancel',    detail: 'Cancel current turn' },
  { label: '/help',      detail: 'Show available commands' },
  { label: '/stats',     detail: 'Show session stats' },
  { label: '/export',    detail: 'Export conversation' },
  { label: '/workspace', detail: 'Switch workspace' },
  { label: '/session',   detail: 'List or switch sessions' },
  { label: '/init',      detail: 'Initialize project rules' },
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
interface ShellInputProps {
  value?: string;
  onChange?: (text: string) => void;
  onSubmit?: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  ghostText?: string;
  commandHistory?: string[];
  autoFocus?: boolean;
}

const ShellInput = forwardRef<{ focus: () => void; blur: () => void }, ShellInputProps>(function ShellInput({
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
  const placeholderCompartment = useRef(new Compartment());
  const themeCompartment = useRef(new Compartment());
  const onChangeRef = useRef(onChange);
  const onSubmitRef = useRef(onSubmit);
  const disabledRef = useRef(disabled);
  const ghostTextRef = useRef(ghostText);
  const historyRef = useRef(commandHistory);
  const historyIdxRef = useRef(-1);       // -1 = not navigating history
  const savedInputRef = useRef('');        // saved input before history nav
  const focusedRef = useRef(false);        // track focus for ghost text visibility
  const [focused, setFocused] = useState(false);
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
          if (update.focusChanged) {
            const hasFocus = update.view.hasFocus;
            focusedRef.current = hasFocus;
            setFocused(hasFocus);
          }
        }),
        placeholderCompartment.current.of(placeholderExt(placeholder)),
        StreamLanguage.define(shell),
        shellKeymap,
        autocompletion({ override: [shellCompletions] }),
        highlightSpecialChars(),
        dropCursor(),
        drawSelection({ cursorColor: '#ffff00', cursorBlinkRate: 530 } as Parameters<typeof drawSelection>[0]),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        themeCompartment.current.of(shellTheme()),
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
          drop: (e) => {
            if (disabledRef.current) {
              e.preventDefault();
            }
          },
        }),
      ],
    });

    // Destroy previous view if re-mounting
    if (viewRef.current) {
      viewRef.current.destroy();
    }

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });
    viewRef.current = view;

    // Auto-focus after mount
    if (autoFocus && !disabled) {
      // Small delay to let the editor settle
      setTimeout(() => view.focus(), 50);
    }

    return () => {
      view.destroy();
      viewRef.current = null;
    };
  // Editor only recreated when `disabled` changes — other deps use
  // separate effects or refs to update the existing editor in place.
  }, [disabled]);

  // --- sync placeholder changes without full editor rebuild ---
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: placeholderCompartment.current.reconfigure(
        placeholderExt(placeholder)
      ),
    });
  }, [placeholder]);
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const currentDoc = view.state.doc.toString();
    if (currentDoc !== value) {
      view.dispatch({
        changes: { from: 0, to: view.state.doc.length, insert: value },
      });
    }
  }, [value]);

  // --- sync theme compartment on data-theme attribute change ---------------
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    let raf = null;
    const reconfigure = () => {
      if (raf != null) return;
      raf = requestAnimationFrame(() => {
        raf = null;
        if (viewRef.current) {
          viewRef.current.dispatch({
            effects: themeCompartment.current.reconfigure(shellTheme()),
          });
        }
      });
    };
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        if (m.attributeName === 'data-theme') {
          reconfigure();
          break;
        }
      }
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  // --- sync editable compartment -------------------------------------------
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: editableCompartment.current.reconfigure(
        EditorView.editable.of(!disabled)
      ),
    });
  }, [disabled]);

  // --- toggle placeholder when ghost text is visible (avoid overlap) -----
  const showGhost = focused && value === '' && ghostText && !disabled;

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: placeholderCompartment.current.reconfigure(
        placeholderExt(showGhost ? '' : placeholder)
      ),
    });
  }, [showGhost, placeholder]);

  // -- ghost text (dimmed text shown when input is empty) -------------------

  return (
    <div style={{ position: 'relative', flex: 1 }}>
      <div ref={containerRef} style={{ height: '100%' }} />
      {showGhost && (
        <div
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            pointerEvents: 'none',
            display: 'flex',
            alignItems: 'center',
            padding: '0 12px',
            color: readThemeVars().dim,
            fontFamily: `"${readThemeVars().fontFamily}", monospace`,
            fontSize: readThemeVars().fontSize,
            opacity: 0.5,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          {ghostText}
        </div>
      )}
    </div>
  );
});

export default ShellInput;
