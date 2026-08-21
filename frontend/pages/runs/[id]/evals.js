/* MERIDIAN - Run Evals Page (eval results with human-friendly scores) */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function RunEvals() {
  const url = new URL(window.location.href);
  const runId = url.pathname.split('/').pop();

  const [evals, setEvals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchEvals();
  }, [runId]);

  const fetchEvals = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/runs/' + runId + '/evals', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load evals');
      const data = await res.json();
      setEvals(data || []);
    } catch (err) {
      setError(err.message);
      setEvals([]);
    }
    setLoading(false);
  };

  const verdictLabel = function (verdict, score) {
    if (verdict === 'pass') return 'Pass • Great';
    if (verdict === 'fail') return 'Fail • Needs Improvement';
    if (score !== undefined && score !== null) {
      const s = Math.round(score);
      if (s >= 8) return 'Pass • ' + s + '/10 • Excellent';
      if (s >= 5) return 'Pass • ' + s + '/10 • Acceptable';
      return 'Fail • ' + s + '/10 • Needs Improvement';
    }
    return 'Indeterminate';
  };

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Evals</title>
        </Head>
        <p>Loading eval results...</p>
      </div>
    );
  }

  if (!runId) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Evals</title>
        </Head>
        <p style={{ color: '#ef4444' }}>Missing run ID.</p>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Eval Results</title>
        <meta name="description" content={`Eval results for run ${runId}`} />
      </Head>

      <main style={{ maxWidth: '1200px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Eval Results</title>
        </Head>

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.75rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Eval Results
          </h1>
          <p style={{ color: '#64748b', margin: '0.5rem 0' }}>
            Run: {runId.substring(0, 8)}...
          </p>
        </div>

        <div style={{ marginBottom: '2rem', backgroundColor: '#1e293b', borderRadius: '8px', padding: '1.5rem', border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: '600', margin: '0 0 1rem', color: '#f1f5f9' }}>
            Eval Definitions ({evals.length})
          </h2>
          {evals.length === 0 && (
            <p style={{ color: '#64748b', margin: '1rem 0' }}>
              No evals have been run for this mission.
            </p>
          )}
          <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
            {evals.map((eval_) => {
              const label = verdictLabel(eval_.verdict, eval_.score);
              return (
                <div key={eval_.id} style={{
                  marginBottom: '0.75rem',
                  padding: '0.5rem 0',
                  borderBottom: '1px solid #334155',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                }}>
                  <div style={{ color: '#e2e8f0' }}>
                    <strong>{eval_.name}</strong>
                    <span style={{ color: '#64748b', fontSize: '0.8rem', marginLeft: '0.5rem' }}>
                      ({eval_.eval_type}){eval_.scope ? ' - scope: ' + eval_.scope : ''}
                    </span>
                  </div>
                  <span style={{
                    color: eval_.verdict === 'pass' ? '#10b981' : eval_.verdict === 'fail' ? '#ef4444' : '#6b7280',
                    fontWeight: '500',
                    fontSize: '0.8rem',
                  }}>
                    {label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ marginTop: '2rem', color: '#64748b', fontSize: '0.875rem' }}>
          <strong>Eval verdicts:</strong> 
          <span style={{ color: '#10b981', marginRight: '0.5rem' }}>pass</span>
          <span style={{ color: '#ef4444', marginRight: '0.5rem' }}>fail</span>
          <span style={{ color: '#6b7280' }}>indeterminate</span>
        </div>
      </main>
    </div>
  );
}