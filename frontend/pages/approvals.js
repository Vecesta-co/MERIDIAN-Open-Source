/* MERIDIAN - Approvals Inbox Page (polling every 5s with timeout countdown) */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function ApprovalsInbox() {
  const [approvals, setApprovals] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [pollInterval, setPollInterval] = useState(null);

  useEffect(() => {
    fetchApprovals();
    // Set up polling every 5 seconds, stop when no pending approvals remain
    const intervalId = setInterval(fetchApprovals, 5000);
    setPollInterval(intervalId);
    return () => clearInterval(intervalId);
  }, []);

  // Format milliseconds to HH:MM:SS
  const formatTime = function (ms) {
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return (
      (hours ? hours + ':' : '') +
      (minutes ? (minutes < 10 ? '0' : '') + minutes + ':' : '0:') +
      (seconds < 10 ? '0' : '') + seconds
    );
  };

  const fetchApprovals = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/approvals?status=pending', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load approvals');
      const data = await res.json();
      
      // Enrich each approval with mission name and timeout countdown
      const enriched = await Promise.all(
        (data || []).map(async (approval) => {
          // Get mission name from the run
          let missionName = '—';
          let stepName = approval.step_key || '—';
          let outputPreview = '—';
          let timePending = '—';
          let timeoutExpiry = null;

          if (approval.run_id) {
            try {
              const runRes = await fetch('/runs/' + approval.run_id, {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
              });
              if (runRes.ok) {
                const runData = await runRes.json();
                missionName = runData.mission_name || '—';
              }
            } catch (e) {
              // Swallow — mission name optional
            }
            // Get step name from context_json
            if (approval.context_json && approval.context_json.step_name) {
              stepName = approval.context_json.step_name;
            }
            // Output preview
            if (approval.original_output) {
              const outputStr = JSON.stringify(approval.original_output);
              outputPreview = outputStr.length > 200 ? outputStr.substring(0, 200) + '...' : outputStr;
            }
            // Timeout countdown
            if (approval.requested_at && approval.timeout_seconds) {
              const requested = new Date(approval.requested_at).getTime();
              const timeoutMs = approval.timeout_seconds * 1000;
              const expiry = requested + timeoutMs;
              const now = Date.now();
              const remaining = expiry - now;
              timeoutExpiry = new Date(expiry);
              if (remaining > 0) {
                timePending = formatTime(remaining);
              } else {
                timePending = 'Expired';
              }
            }
          }

          return {
            ...approval,
            missionName,
            stepName,
            outputPreview,
            timePending,
            timeoutExpiry,
          };
        })
      );

      setApprovals(enriched);
    } catch (err) {
      setError(err.message);
      setApprovals([]);
    }
    setLoading(false);
  };

  const handleApprove = async (approvalId) => {
    if (!confirmAction('Approve this approval? This will resume the associated run.')) return;
    try {
      const res = await fetch('/approvals/' + approvalId + '/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ decision: 'approved' }),
      });
      if (!res.ok) throw new Error('Failed to approve');
      window.log('Approval approved successfully.');
      fetchApprovals(); // Refresh list
    } catch (err) {
      window.alert('Failed to approve: ' + err.message);
    }
  };

  const handleReject = async (approvalId) => {
    if (!confirmAction('Reject this approval? This will mark the associated run as failed.')) return;
    try {
      const res = await fetch('/approvals/' + approvalId + '/decide', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ decision: 'rejected' }),
      });
      if (!res.ok) throw new Error('Failed to reject');
      window.log('Approval rejected successfully.');
      fetchApprovals(); // Refresh list
    } catch (err) {
      window.alert('Failed to reject: ' + err.message);
    }
  };

  const handleModify = async (approvalId) => {
    const modifiedOutput = window.prompt('Enter modified output as JSON:');
    if (modifiedOutput === null || !modifiedOutput.trim()) {
      window.alert('Modified output cancelled.');
      return;
    }
    try {
      const parsed = JSON.parse(modifiedOutput);
      try {
        const res = await fetch('/approvals/' + approvalId + '/decide', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ decision: 'modify', decision_json: { modified_output: parsed } }),
        });
        if (!res.ok) throw new Error('Failed to modify');
        window.log('Approval modified successfully.');
        fetchApprovals(); // Refresh list
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
          <title>MERIDIAN - Approvals</title>
        </Head>
        <p>Loading approvals...</p>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Approvals Inbox</title>
        <meta name="description" content="Human-in-the-loop approval inbox" />
      </Head>

      <main style={{ maxWidth: '1000px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Approvals Inbox</title>
        </Head>

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.75rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Approvals Inbox
          </h1>
          <p style={{ color: '#64748b', margin: '0.5rem 0' }}>
            Polling every 5s. Shows mission name, step, time pending, and timeout countdown.
          </p>
        </div>

        {loading && (
          <div style={{ marginBottom: '2rem' }}>
            <p>Loading approvals...</p>
          </div>
        )}

        {approvals.length === 0 && (
          <div style={{ color: '#64748b', margin: '2rem 0' }}>
            No pending approvals.
            <br/>All runs are complete or being processed.
          </div>
        )}

        <div style={{ overflowX: 'auto', marginBottom: '2rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem' }}>
            <thead>
              <tr style={{ backgroundColor: '#1e293b', color: '#e2e8f0', padding: '0.75rem' }}>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Mission</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Step</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Output Preview</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Time Pending</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Decision</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}></th>
              </tr>
            </thead>
            <tbody>
              {approvals.map((approval) => (
                <tr key={approval.id} style={{ backgroundColor: '#1e293b', padding: '0.75rem', borderBottom: '1px solid #334155' }}>
                  <td style={{ padding: '0.5rem' }}>
                    <span style={{ color: '#e2e8f0', fontWeight: '500' }}>{approval.missionName || '—'}</span>
                  </td>
                  <td style={{ padding: '0.5rem' }}>
                    <span style={{ color: '#f59e0b' }}>{approval.stepName || '—'}</span>
                  </td>
                  <td style={{ padding: '0.5rem' }}>
                    <span style={{ color: '#64748b', fontSize: '0.75rem', whiteSpace: 'pre-wrap' }}>{approval.outputPreview || '—'}</span>
                  </td>
                  <td style={{ padding: '0.5rem', color: approval.timePending === 'Expired' ? '#ef4444' : '#f59e0b' }}>
                    {approval.timePending || '—'}
                  </td>
                  <td style={{ padding: '0.5rem', color: '#f59e0b' }}>
                    {approval.status}
                  </td>
                  <td style={{ padding: '0.5rem', textAlign: 'right' }}>
                    {approval.status === 'pending' && (
                      <>
                        <button
                          onClick={() => handleApprove(approval.id)}
                          title="Approve"
                          style={{
                            marginRight: '0.25rem',
                            background: 'transparent',
                            border: '1px solid #10b981',
                            color: '#10b981',
                            padding: '0.25rem 0.5rem',
                            borderRadius: '4px',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                          }}
                        >
                          ✓
                        </button>
                        <button
                          onClick={() => handleReject(approval.id)}
                          title="Reject"
                          style={{
                            marginRight: '0.25rem',
                            background: 'transparent',
                            border: '1px solid #ef4444',
                            color: '#ef4444',
                            padding: '0.25rem 0.5rem',
                            borderRadius: '4px',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                          }}
                        >
                          ✗
                        </button>
                        <button
                          onClick={() => handleModify(approval.id)}
                          title="Modify"
                          style={{
                            background: 'transparent',
                            border: '1px solid #f59e0b',
                            color: '#f59e0b',
                            padding: '0.25rem 0.5rem',
                            borderRadius: '4px',
                            cursor: 'pointer',
                            fontSize: '0.75rem',
                          }}
                        >
                          ⇧
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ color: '#64748b', fontSize: '0.875rem' }}>
          Polling every 5 seconds — automatically stops when no pending approvals remain
        </div>
      </main>
    </div>
  );
}