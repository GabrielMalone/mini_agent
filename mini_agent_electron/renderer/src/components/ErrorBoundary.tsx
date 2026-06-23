import { Component, type ReactNode, type ErrorInfo } from 'react';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  private _errorHandler: ((event: ErrorEvent) => void) | null = null;
  private _unhandledHandler: ((event: PromiseRejectionEvent) => void) | null = null;

  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  componentDidMount(): void {
    this._errorHandler = (event: ErrorEvent) => {
      this.setState({ error: event.error || new Error(event.message || 'Unhandled error') });
    };
    window.addEventListener('error', this._errorHandler);
    this._unhandledHandler = (event: PromiseRejectionEvent) => {
      this.setState({ error: event.reason || new Error('Unhandled promise rejection') });
    };
    window.addEventListener('unhandledrejection', this._unhandledHandler);
  }

  componentWillUnmount(): void {
    if (this._errorHandler) {
      window.removeEventListener('error', this._errorHandler);
    }
    if (this._unhandledHandler) {
      window.removeEventListener('unhandledrejection', this._unhandledHandler);
    }
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div style={{
          padding: '2rem',
          color: 'var(--red)',
          fontFamily: 'var(--font-family)',
          fontSize: 'var(--font-size)',
        }}>
          <h3 style={{ marginBottom: '1rem' }}>Something broke</h3>
          <pre style={{ whiteSpace: 'pre-wrap', color: 'var(--dim)' }}>
            {this.state.error.message}
          </pre>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              marginTop: '1rem',
              padding: '6px 16px',
              background: 'var(--accent)',
              color: 'var(--bg)',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
              fontFamily: 'var(--font-family)',
            }}
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
