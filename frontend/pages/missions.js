/* MERIDIAN - Missions List Page */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function MissionsList() {
  const [missions, setMissions] = useState([]);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [stateFilter, setStateFilter] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchMissions();
  }, [page, pageSize, stateFilter]);

  const fetchMissions = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/missions', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to list missions');
      const data = await res.json();
      setMissions(data || []);
    } catch (err) {
      setError(err.message);
      setMissions([]);
    }
    setLoading(false);
  };

  const handleCreate = async () => {
    const yaml = window.prompt('Enter mission YAML (or leave blank for JSON):');
    if (yaml === null) return;

    if (yaml.trim()) {
      try {
        const res = await fetch('/missions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ yaml_text: yaml }),
        });
        if (!res.ok) throw new Error('Failed to create mission');
        window.alert('Mission created successfully!');
        fetchMissions();
      } catch (err) {
        window.alert('Error: ' + err.message);
      }
    } else {
      const name = window.prompt('Enter mission name:');
      if (!name) return;
      const goal = window.prompt('Enter mission goal:');
      if (!goal) return;
      const stepsJson = window.prompt('Enter steps JSON (or cancel):');
      if (stepsJson === null) return;
      try {
        const steps = JSON.parse(stepsJson);
        try {
          const res = await fetch('/missions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({ name, goal, steps }),
          });
          if (!res.ok) throw new Error('Failed to create mission');
          window.alert('Mission created successfully!');
          fetchMissions();
        } catch (err) {
          window.alert('Error: ' + err.message);
        }
      } catch (err) {
        window.alert('Invalid JSON. Mission creation cancelled.');
      }
    }
  };

  const handleDelete = async (id) => {
    if (!confirmAction('Are you sure you want to delete mission ' + id + '? This action cannot be undone.')) return;
    try {
      const res = await fetch('/missions/' + id, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to delete mission');
      window.alert('Mission deleted successfully.');
      fetchMissions();
    } catch (err) {
      window.alert('Failed to delete mission: ' + err.message);
    }
  };

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Missions</title>
        </Head>
        <p>Loading missions...</p>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Missions</title>
        <meta name="description" content="Manage AI Agent Operations Missions" />
      </Head>

      <main style={{ maxWidth: '1200px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Missions</title>
        </Head>

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '2rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Missions
          </h1>
          <p style={{ color: '#94a3b8', margin: '0 0 1.5rem' }}>
            Manage your AI agent operation missions
          </p>
          <button
            onClick={handleCreate}
            style={{
              background: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
              color: 'white',
              padding: '0.75rem 1.5rem',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: '500',
              fontSize: '1rem',
            }}
          >
            Create Mission
          </button>
        </div>

        {missions.length === 0 && (
          <p style={{ color: '#64748b', margin: '2rem 0' }}>
            No missions found. <a href="/runs/new" style={{ color: '#3b82f6' }}>Create your first mission</a>.
          </p>
        )}

        <div style={{ overflowX: 'auto', marginTop: '2rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '1rem' }}>
            <thead>
              <tr style={{ backgroundColor: '#1e293b', color: '#e2e8f0', padding: '0.75rem' }}>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Name</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>State</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Steps</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}>Version</th>
                <th style={{ padding: '0.5rem', textAlign: 'left' }}></th>
              </tr>
            </thead>
            <tbody>
              {missions.map((mission) => (
                <tr key={mission.id} style={{ backgroundColor: '#1e293b', padding: '0.75rem', borderBottom: '1px solid #334155' }}>
                  <td style={{ padding: '0.5rem' }}>
                    <a
                      href={`/missions/${mission.id}`}
                      style={{ color: '#3b82f6', textDecoration: 'none', fontWeight: '500' }}
                    >
                      {mission.name}
                    </a>
                  </td>
                  <td style={{ padding: '0.5rem', color: '#10b981' }}>
                    {mission.state || 'draft'}
                  </td>
                  <td style={{ padding: '0.5rem' }}>
                    {mission.steps ? mission.steps.length : 0} steps
                  </td>
                  <td style={{ padding: '0.5rem' }}>
                    {mission.version || '?'}
                  </td>
                  <td style={{ padding: '0.5rem', textAlign: 'right' }}>
                    <button
                      onClick={() => window.location.href = `/missions/${mission.id}/publish`}
                      title="Publish"
                      style={{
                        marginRight: '0.25rem',
                        background: 'transparent',
                        border: '1px solid #3b82f6',
                        color: '#3b82f6',
                        padding: '0.25rem 0.5rem',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontSize: '0.75rem',
                      }}
                    >
                      Publish
                    </button>
                    <button
                      onClick={() => window.location.href = `/missions/${mission.id}/clone`}
                      title="Clone"
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
                      Clone
                    </button>
                    <button
                      onClick={() => handleDelete(mission.id)}
                      title="Delete"
                      style={{
                        background: 'transparent',
                        border: '1px solid #ef4444',
                        color: '#ef4444',
                        padding: '0.25rem 0.5rem',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        fontSize: '0.75rem',
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div style={{ marginTop: '2rem', color: '#64748b', fontSize: '0.875rem' }}>
          Page {page} of {Math.max(1, Math.ceil((missions.length || 0) / pageSize))}
          &nbsp;&nbsp;
          <select
            onChange={(e) => setPageSize(parseInt(e.target.value, 10))}
            style={{
              backgroundColor: '#1e293b',
              color: '#e2e8f0',
              border: '1px solid #334155',
              borderRadius: '4px',
              padding: '0.25rem 0.5rem',
            }}
          >
            <option value={10}>10</option>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
          </select>
        </div>
      </main>
    </div>
  );
}