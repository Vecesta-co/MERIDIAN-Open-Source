/* MERIDIAN - Approval Detail Page (approve/reject/modify) */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function ApprovalDetail() {
  const url = new URL(window.location.href);
  const approvalId = url.pathname.split('/').pop();

  const [approval, setApproval] = useState(null);
  const [run, setRun] = useState(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!approvalId) {
      setError('Missing approval ID');
      setLoading(false);
      return;
    }
    fetchApproval();
  }, [approvalId]);

  const fetchApproval = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/approvals/' + approvalId, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load approval');
      const data = await res.json();
      setApproval(data);
    } catch (err) {
      setError(err.message);
      setApproval(null);
    }
    setLoading(false);
  };

  const fetchRun = async () => {
    if (!approval) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/runs/' + approval.run_id, {
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

  useEffect(() => {
    fetchRun();
  }, [approval]);

  const handleApprove = async () => {
    if (!approval) return;
    if (!confirmAction('Approve this approval? This will resume the associated run.')) return;
    try {
      const res = await fetch('/approvals/' + approval.id + '/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ decision: 'approved' }),
      });
      if (!res.ok) throw new Error('Failed to approve');
      window.log('Approval approved successfully.');
      fetchApproval();
      fetchRun();
    } catch (err) {
      window.alert('Failed to approve: ' + err.message);
    }
    setLoading(false);
  };

  const handleReject = async () => {
    if (!approval) return;
    if (!confirmAction('Reject this approval? This will mark the associated run as failed.')) return;
    try {
      const res = await fetch('/approvals/' + approval.id + '/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ decision: 'rejected' }),
      });
      if (!res.ok) throw new Error('Failed to reject');
      window.log('Approval rejected successfully.');
      fetchApproval();
      fetchRun();
    } catch (err) {
      window.alert('Failed to reject: ' + err.message);
    }
    setLoading(false);
  };

  const handleModify = async () => {
    if (!approval) return;
    const modifiedOutput = window.prompt('Enter modified output as JSON:');
    if (modifiedOutput === null || !modifiedOutput.trim()) {
      window.alert('Modified output cancelled.');
      return;
    }
    try {
      const parsed = JSON.parse(modifiedOutput);
      try {
        const res = await fetch('/approvals/' + approval.id + '/decide', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ decision: 'modify', decision_json: { modified_output: parsed } }),
        });
        if (!res.ok) throw new Error('Failed to modify');
        window.log('Approval modified successfully.');
        fetchApproval();
        fetchRun();
      } catch (err) {
        window.alert('Failed to modify: ' + err.message);
      }
    } catch (err) {
      window.alert('Invalid JSON. Please enter valid JSON.');
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Approval</title>
        </Head>
        <p>Loading approval detail...</p>
      </div>
    );
  }

  if (!approval) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Approval</title>
        </Head>
        <p style={{ color: '#ef4444' }}>{error || 'Approval not found'}</p>
      </div>
    );
  }

  useEffect(() => {
    fetchRun();
  }, [approval.run_id]);

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Approval: {approval.id ? approval.id.substring(0, 8) + '...' : '—'}</title>
        <meta name="description" content="Approval detail - human decision" />
      </Head>

      <main style={{ maxWidth: '1000px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Approval Detail</title>
        </Head>

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.75rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Approval: {approval.id ? approval.id.substring(0, 8) + '...' : '—'}
          </h1>
        </div>

        <div style={{ marginBottom: '2rem', backgroundColor: '#1e293b', borderRadius: '8px', padding: '1.5rem', border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: '600', margin: '0 0 1rem', color: '#f1f5f9' }}>
            Approval Details
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
            <div>
              <strong style={{ color: '#94a3b8', fontSize: '0.875rem' }}>Status:</strong>
              <span style={{ color: approval.status === 'pending' ? '#f59e0b' : approval.status === 'approved' ? '#10b981' : approval.status === 'rejected' ? '#ef4444' : '#a855f7', fontWeight: '500' }}>
                {approval.status}
              </span>
            </div>
            <div>
              <strong style={{ color: '#94a3b8', fontSize: '0.875rem' }}>Step:</strong>
              <span style={{ color: '#e2e8f0' }}>{approval.step_key || '—'}</span>
            </div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            <div>
              <strong style={{ color: '#94a3b8', fontSize: '0.875rem' }}>Run ID:</strong>
              <span style={{ color: '#e2e8f0' }}>{approval.run_id}</span>
            </div>
            <div>
              <strong style={{ color: '#94a3b8', fontSize: '0.875rem' }}>Decided By:</strong>
              <span style={{ color: '#e2e8f0' }}>{approval.decided_by || '—'}</span>
            </div>
          </div>
          {approval.context_json && (
            <div style={{ marginTop: '1rem', paddingTop: '1rem', borderTop: '1px solid #334155' }}>
              <strong style={{ color: '#94a3b8', fontSize: '0.875rem' }}>Context:</strong>
              <pre style={{ color: '#94a3b8', fontSize: '0.75rem', backgroundColor: '#0f172a', padding: '0.5rem', borderRadius: '4px', overflowX: 'auto' }}>
                {JSON.stringify(approval.context_json, null, 2)}
              </pre>
            </div>
          )}
          {approval.decision_notes && (
            <div style={{ marginTop: '1rem', padding: '0.5rem', backgroundColor: '#334155', borderRadius: '4px' }}>
              <strong style={{ color: '#94a3b8', fontSize: '0.875rem' }}>Notes:</strong>
              <span style={{ color: '#e2e8f0', fontSize: '0.875rem' }}>{approval.decision_notes}</span>
            </div>
          )}
        </div>

        <div style={{ marginTop: '1.5rem', padding: '1rem', backgroundColor: '#334155', borderRadius: '6px' }}>
          <h3 style={{ color: '#f1f5f9', margin: '0 0 0.5rem' }}>
            Outputs
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '0.5rem' }}>
            <div>
              <strong style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Original:</strong>
              <span style={{ color: '#64748b', fontSize: '0.75rem', whiteSpace: 'pre-wrap' }}>{approval.original_output ? JSON.stringify(approval.original_output).substring(0, 200) + '...' : '—'}</span>
            </div>
            <div>
              <strong style={{ color: '#94a3b8', fontSize: '0.8rem' }}>Modified:</strong>
              <span style={{ color: '#64748b', fontSize: '0.75rem', whiteSpace: 'pre-wrap' }}>{approval.modified_output ? JSON.stringify(approval.modified_output).substring(0, 200) + '...' : '—'}</span>
            </div>
          </div>
        </div>

        {approval.status === 'pending' && (
          <div style={{ marginTop: '2rem', textAlign: 'center' }}>
            <button
              onClick={handleApprove}
              style={{
                background: 'linear-gradient(135deg, #10b981, #059669)',
                color: 'white',
                padding: '0.75rem 1.5rem',
                borderRadius: '8px',
                border: 'none',
                cursor: 'pointer',
                fontWeight: '500',
                fontSize: '1rem',
              }}
            >
              Approve
            </button>
            <button
              onClick={handleReject}
              style={{
                marginLeft: '1rem',
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
              Reject
            </button>
            <button
              onClick={handleModify}
              style={{
                marginLeft: '1rem',
                background: 'linear-gradient(135deg, #f59e0b, #d97706)',
                color: 'white',
                padding: '0.75rem 1.5rem',
                borderRadius: '8px',
                border: 'none',
                cursor: 'pointer',
                fontWeight: '500',
                fontSize: '1rem',
              }}
            >
              Modify
            </button>
          </div>
        )}
      </main>
    </div>
  );
}