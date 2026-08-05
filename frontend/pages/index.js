import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function Home() {
  const [apiStatus, setApiStatus] = useState('checking...');
  const [apiVersion, setApiVersion] = useState('');

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then((data) => {
        setApiStatus(data.status);
        setApiVersion(data.version);
      })
      .catch(() => {
        setApiStatus('unreachable');
      });
  }, []);

  return (
    <div style={styles.container}>
      <Head>
        <title>MERIDIAN — AI Agent Operations Platform</title>
        <meta name="description" content="MERIDIAN Platform Frontend" />
        <link rel="icon" href="/favicon.ico" />
      </Head>

      <main style={styles.main}>
        <h1 style={styles.title}>🧠 MERIDIAN</h1>
        <p style={styles.subtitle}>AI Agent Operations Platform</p>

        <div style={styles.card}>
          <h2 style={styles.cardTitle}>System Status</h2>
          <div style={styles.statusRow}>
            <span style={styles.label}>API Status:</span>
            <span style={{
              ...styles.value,
              color: apiStatus === 'healthy' ? '#22c55e' : apiStatus === 'checking...' ? '#f59e0b' : '#ef4444'
            }}>
              {apiStatus}
            </span>
          </div>
          {apiVersion && (
            <div style={styles.statusRow}>
              <span style={styles.label}>API Version:</span>
              <span style={styles.value}>{apiVersion}</span>
            </div>
          )}
        </div>

        <div style={styles.card}>
          <h2 style={styles.cardTitle}>Phase 0 — Foundation Complete</h2>
          <p style={styles.cardText}>
            MERIDIAN backend API is running. Frontend skeleton is operational.
            Full dashboard coming in Phase 7.
          </p>
        </div>

        <div style={styles.footer}>
          <p>MERIDIAN v0.1.0 — Open Source AI Agent Operations Platform</p>
        </div>
      </main>
    </div>
  );
}

const styles = {
  container: {
    minHeight: '100vh',
    backgroundColor: '#0f172a',
    color: '#e2e8f0',
    fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif',
  },
  main: {
    maxWidth: '800px',
    margin: '0 auto',
    padding: '4rem 2rem',
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    gap: '2rem',
  },
  title: {
    fontSize: '3rem',
    fontWeight: '700',
    margin: 0,
    background: 'linear-gradient(135deg, #60a5fa, #a78bfa)',
    WebkitBackgroundClip: 'text',
    WebkitTextFillColor: 'transparent',
  },
  subtitle: {
    fontSize: '1.2rem',
    color: '#94a3b8',
    margin: 0,
  },
  card: {
    width: '100%',
    backgroundColor: '#1e293b',
    borderRadius: '12px',
    padding: '1.5rem',
    border: '1px solid #334155',
  },
  cardTitle: {
    fontSize: '1.1rem',
    fontWeight: '600',
    margin: '0 0 1rem 0',
    color: '#f1f5f9',
  },
  cardText: {
    color: '#94a3b8',
    lineHeight: '1.6',
    margin: 0,
  },
  statusRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '0.5rem 0',
    borderBottom: '1px solid #334155',
  },
  label: {
    color: '#94a3b8',
    fontWeight: '500',
  },
  value: {
    fontWeight: '600',
    textTransform: 'capitalize',
  },
  footer: {
    marginTop: '2rem',
    textAlign: 'center',
    color: '#64748b',
    fontSize: '0.875rem',
  },
};
