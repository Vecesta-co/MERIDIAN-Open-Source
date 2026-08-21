/* MERIDIAN - Run New Page (Pick mission + start) */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function RunNew() {
  const [missions, setMissions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedMission, setSelectedMission] = useState(null);

  useEffect(() => {
    fetchMissions();
  }, []);

  const fetchMissions = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/missions?state=published', {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load missions');
      const data = await res.json();
      setMissions(data || []);
    } catch (err) {
      setError(err.message);
      setMissions([]);
      window.alert('Failed to load missions: ' + err.message);
    }
    setLoading(false);
  };

  const handleStartRun = async () => {
    if (!selectedMission) {
      window.alert('Please select a mission first.');
      return;
    }
    try {
      const res = await fetch('/runs/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ mission_id: selectedMission.id }),
      });
      if (!res.ok) throw new Error('Failed to start run');
      const data = await res.json();
      window.alert('Run started successfully! Run ID: ' + data.id);
      window.location.href = '/runs/' + data.id;
    } catch (err) {
      window.alert('Failed to start run: ' + err.message);
    }
    setLoading(false);
  };

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Start Run</title>
        </Head>
        <p>Loading missions...</p>
      </div>
    );
  }

  if (!selectedMission && missions.length === 0) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Start Run</title>
        </Head>
        <p style={{ color: '#64748b' }}>No published missions found. <a href="/missions">Create a mission first</a>.</p>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Start New Run</title>
        <meta name="description" content="Pick a mission and start a new run" />
      </Head>

      <main style={{ maxWidth: '1000px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Start New Run</title>
        </Head>

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.75rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Start New Run
          </h1>
          <p style={{ color: '#64748b', margin: '0.5rem 0' }}>
            Select a published mission to begin a run
          </p>
        </div>

        {missions.length === 0 && (
          <p style={{ color: '#64748b', margin: '2rem 0' }}>
            No published missions found. <a href="/missions">Create a mission first</a>.
          </p>
        )}

        <div style={{ marginBottom: '2rem' }}>
          <select
            value={selectedMission ? selectedMission.id : ''}
            onChange={(e) => {
              const id = e.target.value;
              setSelectedMission(id ? missions.find(m => m.id === id) : null);
            }}
            style={{
              width: '100%',
              backgroundColor: '#1e293b',
              color: '#e2e8f0',
              border: '1px solid #334155',
              borderRadius: '4px',
              padding: '0.75rem',
              fontSize: '1rem',
              marginBottom: '1rem',
            }}
          >
            <option value="">-- Select a mission --</option>
            {missions.map((mission) => (
              <option key={mission.id} value={mission.id}>
                {mission.name}
              </option>
            ))}
          </select>
        </div>

        {selectedMission && (
          <div>
            <p style={{ color: '#10b981', margin: '0.5rem 0', fontWeight: '500' }}>
              Mission: {selectedMission.name}
            </p>
            <p style={{ color: '#64748b', margin: '0.25rem 0' }}>
              Goal: {selectedMission.goal || '—'}
            </p>
            <p style={{ color: '#64748b', margin: '0.25rem 0' }}>
              Steps: {selectedMission.steps ? selectedMission.steps.length : 0}
            </p>
          </div>
        )}

        <div style={{ textAlign: 'center' }}>
          <button
            onClick={handleStartRun}
            style={{
              background: 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
              color: 'white',
              padding: '1rem 2rem',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: '500',
              fontSize: '1.1rem',
              marginTop: '1rem',
            }}
            disabled={!selectedMission}
          >
            Start Run
          </button>
        </div>
      </main>
    </div>
  );
}