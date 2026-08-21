/* MERIDIAN - Run Detail Page (Status + Trace Tree + Steps + Auto-Polling + Cancel) */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function RunDetail() {
  const url = new URL(window.location.href);
  const runId = url.pathname.split('/').pop();

  const [run, setRun] = useState(null);
  const [steps, setSteps] = useState([]);
  const [spans, setSpans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [terminalShown, setTerminalShown] = useState(false);

  useEffect(() => {
    if (!runId) {
      setError('Missing run ID');
      setLoading(false);
      return;
    }
    fetchRun();
    fetchSteps();
    fetchSpans();
    // Start polling for status updates — stop on terminal state
    const intervalId = setInterval(async () => {
      try {
        const res = await fetch('/runs/' + runId + '/summary', {
          method: 'GET',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
        });
        if (!res.ok) return;
        const data = await res.json();
        if (data && !error) {
          setRun(data);
          const terminalStates = ['completed', 'failed', 'cancelled', 'timed_out'];
          if (data.status && terminalStates.includes(data.status)) {
            // Show terminal state banner
            setTerminalShown(true);
            // Stop polling
            clearInterval(intervalId);
            window.alert('Run status: ' + data.status);
          }
        }
      } catch (err) {
        console.error('Polling error:', err);
      }
    }, 5000);
    return () => clearInterval(intervalId);
  }, [runId]);

  const fetchRun = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/runs/' + runId, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load run');
      const data = await res.json();
      setRun(data);
    } catch (err) {
      setError(err.message);
      setRun(null);
    }
    setLoading(false);
  };

  const fetchSteps = async () => {
    if (!run) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/runs/' + runId + '/steps', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load steps');
      const data = await res.json();
      setSteps(data || []);
    } catch (err) {
      setError(err.message);
      setSteps([]);
    }
    setLoading(false);
  };

  const fetchSpans = async () => {
    if (!run) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/runs/' + runId + '/trace', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load trace');
      const data = await res.json();
      setSpans(data || []);
    } catch (err) {
      setError(err.message);
      setSpans([]);
    }
    setLoading(false);
  };

  const handleCancel = async () => {
    if (!run) return;
    if (!confirmAction('Are you sure you want to cancel this run?')) return;
    try {
      const res = await fetch('/runs/' + runId, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to cancel run');
      window.alert('Run cancelled successfully.');
      fetchRun();
      fetchSteps();
      fetchSpans();
    } catch (err) {
      window.alert('Failed to cancel run: ' + err.message);
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Run</title>
        </Head>
        <p>Loading run details...</p>
      </div>
    );
  }

  if (!run) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Run</title>
        </Head>
        <p style={{ color: '#ef4444' }}>{error || 'Run not found'}</p>
      </div>
    );
  }

  // Determine if cancel button should show
  const canCancel = run.status !== 'completed' && run.status !== 'failed' && run.status !== 'cancelled' && run.status !== 'timed_out';

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Run: {run.id ? run.id.substring(0, 8) + '...' : '—'}</title>
        <meta name="description" content={`Run detail for ${runId || ''}`} />
      </Head>

      <main style={{ maxWidth: '1200px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Run Detail</title>
        </Head>

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.75rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Run: {run.id ? run.id.substring(0, 8) + '...' : '—'}
          </h1>
          <p style={{ color: '#64748b', margin: '0.5rem 0' }}>
            Mission: {run.mission_name || '—'}
          </p>
          <p style={{ color: '#64748b', margin: '0.25rem 0' }}>
            Status: <span style={{ 
              color: run.status === 'completed' ? '#10b981' : run.status === 'failed' ? '#ef4444' : 
              run.status === 'cancelled' ? '#6b7280' : '#f59e0b',
              fontWeight: '500', 
              textTransform: 'capitalize'
            }}>{run.status || 'pending'}</span>
          </p>
          {run.error_summary && (
            <p style={{ color: '#f87171', margin: '0.5rem 0', fontSize: '0.875rem' }}>
              Error: {run.error_summary}
            </p>
          )}
          {terminalShown && (
            <p style={{ margin: '0.5rem 0', padding: '0.5rem', backgroundColor: '#059669', color: '#d1f7dd', borderRadius: '4px' }}>
              Run has reached a terminal state. Polling stopped.
            </p>
          )}
        </div>

        <div style={{ marginBottom: '2rem', backgroundColor: '#1e293b', borderRadius: '8px', padding: '1.5rem', border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: '600', margin: '0 0 1rem', color: '#f1f5f9' }}>
            Trace Tree ({spans.length} spans)
          </h2>
          {spans.length === 0 && (
            <p style={{ color: '#64748b', margin: '1rem 0' }}>
              No trace data available. Run may still be in progress.
            </p>
          )}
          <div style={{ maxHeight: '400px', overflowY: 'auto', backgroundColor: '#0f172a', borderRadius: '4px', padding: '1rem' }}>
            {spans.map((span) => (
              <div key={span.id} style={{
                marginBottom: '0.75rem',
                padding: '0.5rem 0',
                borderBottom: '1px solid #334155',
                display: 'flex',
                alignItems: 'center',
              }}>
                <span style={{ 
                  width: '20px', 
                  fontFamily: 'monospace', 
                  fontSize: '0.75rem', 
                  color: '#94a3b8' 
                }}>
                  {span.kind === 'run' ? 'RUN' : span.kind === 'step' ? 'STEP' : span.kind || '—'}
                </span>
                <span style={{ flexGrow: 1, color: '#e2e8f0', marginLeft: '0.5rem' }}>
                  {span.name}
                </span>
                <span style={{ 
                  color: '#64748b', 
                  marginLeft: '1rem',
                  fontFamily: 'monospace',
                  fontSize: '0.8rem'
                }}>
                  {span.duration_ms ? (span.duration_ms / 1000).toFixed(1) + 's' : '—'}
                </span>
                <span style={{
                  marginLeft: 'auto',
                  color: span.status === 'ok' ? '#10b981' : span.status === 'error' ? '#ef4444' : '#f59e0b',
                  fontWeight: '500',
                  fontSize: '0.8rem',
                }}>
                  {span.status || '—'}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ marginBottom: '2rem', backgroundColor: '#1e293b', borderRadius: '8px', padding: '1.5rem', border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: '600', margin: '0 0 1rem', color: '#f1f5f9' }}>
            Steps ({steps.length})
          </h2>
          {steps.length === 0 && (
            <p style={{ color: '#64748b', margin: '1rem 0' }}>
              No step data available.
            </p>
          )}
          <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
            {steps.map((step) => (
              <div key={step.id} style={{
                marginBottom: '0.5rem',
                padding: '0.25rem 0',
                borderBottom: '1px solid #334155',
              }}>
                <strong style={{ color: '#e2e8f0' }}>{step.name || step.step_key || '—'}</strong>
                <span style={{ color: '#64748b', marginLeft: '0.5rem', fontSize: '0.8rem' }}>
                  {step.status || '—'}
                </span>
                {step.output_json && step.status === 'completed' && (
                  <span style={{ color: '#3b82f6', fontSize: '0.75rem', marginLeft: '0.5rem' }}>
                    output: {JSON.stringify(step.output_json).substring(0, 100)}...
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        {canCancel && (
          <div style={{ textAlign: 'center' }}>
            <button
              onClick={handleCancel}
              style={{
                background: 'linear-gradient(135deg, #ef4444, #dc2626)',
                color: 'white',
                padding: '0.75rem 1.5rem',
                borderRadius: '8px',
                border: 'none',
                cursor: 'pointer',
                fontWeight: '500',
                fontSize: '1rem',
              }}
            >
              Cancel Run
            </button>
          </div>
        )}
      </main>
    </div>
  );
}