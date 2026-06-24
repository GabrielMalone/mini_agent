import { useMemo } from 'react';

// ---------------------------------------------------------------------------
// PlanPanel -- real-time plan progress + todo tracker
// ---------------------------------------------------------------------------

export interface PlanStep {
  step: string;
  done: boolean;
}

export interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'done';
}

interface Props {
  planSteps: string[];
  planDone: number[];
  todos: TodoItem[];
  theme: string;
}

export default function PlanPanel({ planSteps, planDone, todos, theme }: Props) {
  const doneSet = useMemo(() => new Set(planDone), [planDone]);
  const hasPlan = planSteps.length > 0;
  const hasTodos = todos.length > 0;
  if (!hasPlan && !hasTodos) return null;

  // Render plan steps
  const planLines: Array<{ indent: number; marker: string; text: string; done: boolean }> = [];
  if (hasPlan) {
    for (let i = 0; i < planSteps.length; i++) {
      const done = doneSet.has(i);
      planLines.push({ indent: 0, marker: done ? '\u2713' : '\u25cb', text: planSteps[i], done });
    }
  }

  // Render todo items
  const todoLines: Array<{ indent: number; marker: string; text: string; done: boolean }> = [];
  if (hasTodos) {
    for (const t of todos) {
      const done = t.status === 'done';
      todoLines.push({ indent: 0, marker: done ? '\u2713' : '\u25cb', text: t.content, done });
    }
  }

  const doneCount = hasPlan ? planDone.length : 0;
  const totalCount = hasPlan ? planSteps.length : 0;
  const progress = totalCount > 0 ? Math.round((doneCount / totalCount) * 100) : 0;

  return (
    <div className="plan-panel" data-theme={theme}>
      {/* Plan section */}
      {hasPlan && (
        <div className="plan-panel__section">
          <div className="plan-panel__header">
            <span className="plan-panel__title">Plan</span>
            <span className="plan-panel__progress">
              {doneCount}/{totalCount} ({progress}%)
            </span>
          </div>
          <div className="plan-panel__progress-bar">
            <div
              className="plan-panel__progress-fill"
              style={{ width: `${progress}%` }}
            />
          </div>
          <ul className="plan-panel__list">
            {planLines.map((line, i) => (
              <li
                key={i}
                className={`plan-panel__item${line.done ? ' plan-panel__item--done' : ''}`}
              >
                <span className={`plan-panel__marker${line.done ? ' plan-panel__marker--done' : ''}`}>
                  {line.marker}
                </span>
                <span className="plan-panel__text">{line.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Todo section */}
      {hasTodos && (
        <div className="plan-panel__section">
          <div className="plan-panel__header">
            <span className="plan-panel__title">Todos</span>
            <span className="plan-panel__count">
              {todos.filter((t) => t.status === 'done').length}/{todos.length}
            </span>
          </div>
          <ul className="plan-panel__list">
            {todoLines.map((line, i) => (
              <li
                key={i}
                className={`plan-panel__item${line.done ? ' plan-panel__item--done' : ''}`}
              >
                <span className={`plan-panel__marker${line.done ? ' plan-panel__marker--done' : ''}`}>
                  {line.marker}
                </span>
                <span className="plan-panel__text">{line.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
