/* MERIDIAN - Mission Edit Page (View/Edit YAML, Publish, Clone, Save) */
import Head from 'next/head';
import { useState, useEffect } from 'react';

export default function MissionEdit() {
  const urlParams = new URLSearchParams(window.location.search);
  const missionId = urlParams.get('id');

  const [mission, setMission] = useState(null);
  const [yaml, setYaml] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [successMessage, setSuccessMessage] = useState(null);
  const [isPublished, setIsPublished] = useState(false);
  const [validationError, setValidationError] = useState('');

  useEffect(() => {
    if (!missionId) {
      setError('Missing mission ID');
      setLoading(false);
      return;
    }
    fetchMission();
  }, [missionId]);

  const fetchMission = async () => {
    setLoading(true);
    setError(null);
    setSuccessMessage(null);
    setValidationError('');
    try {
      const res = await fetch('/missions/' + missionId, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to load mission');
      const data = await res.json();
      setMission(data);
      if (data && data.yaml_text) {
        setYaml(data.yaml_text);
      } else if (data) {
        setYaml(missionToYaml(data));
      }
      setIsPublished(data && data.state === 'published');
    } catch (err) {
      setError(err.message);
      setMission(null);
    }
    setLoading(false);
  };

  // Parse YAML textarea into structured mission data
  const parseYaml = function (text) {
    const result = {
      name: '',
      goal: '',
      steps: []
    };

    if (!text) return result;

    // Parse name
    const nameMatch = text.match(/name:\s*"([^"]*)"/);
    if (nameMatch) result.name = nameMatch[1];

    // Parse goal
    const goalMatch = text.match(/goal:\s*"([^"]*)"/);
    if (goalMatch) result.goal = goalMatch[1];

    // Parse steps
    let inSteps = false;
    let currentStep = null;

    const lines = text.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const trimmed = line.trim();

      if (trimmed === 'steps:') {
        inSteps = true;
        continue;
      }

      if (inSteps) {
        if (trimmed.startsWith('- key:')) {
          if (currentStep) {
            result.steps.push(currentStep);
          }
          currentStep = { key: '' };
          const keyMatch = trimmed.match(/-\s+key:\s*"([^"]*)"/);
          if (keyMatch) currentStep.key = keyMatch[1];
          continue;
        }

        if (currentStep) {
          if (trimmed.startsWith('name:')) {
            const nm = trimmed.match(/name:\s*"([^"]*)"/);
            if (nm) currentStep.name = nm[1];
          } else if (trimmed.startsWith('prompt_template:')) {
            const pt = trimmed.match(/prompt_template:\s*"([^"]*)"/);
            if (pt) currentStep.prompt_template = pt[1];
          } else if (trimmed.startsWith('agent_key:')) {
            const ak = trimmed.match(/agent_key:\s*"([^"]*)"/);
            if (ak) currentStep.agent_key = ak[1];
          } else if (trimmed.startsWith('tool_refs:')) {
            const tr = trimmed.match(/tool_refs:\s*\[?([^\]]*)\]?/);
            if (tr) {
              const tools = tr[1].split(',').map(t => t.trim()).filter(t => t);
              currentStep.tool_refs = tools;
            }
          } else if (trimmed.startsWith('approval_required:')) {
            const ar = trimmed.match(/approval_required:\s*(\w+)/);
            if (ar) currentStep.approval_required = ar[1] === 'true';
          } else if (trimmed.startsWith('max_retries:')) {
            const mr = trimmed.match(/max_retries:\s*(\d+)/);
            if (mr) currentStep.max_retries = parseInt(mr[1], 10);
          } else if (trimmed.startsWith('timeout_seconds:')) {
            const ts = trimmed.match(/timeout_seconds:\s*(\d+)/);
            if (ts) currentStep.timeout_seconds = parseInt(ts[1], 10);
          }
        }
      }
    }
    if (currentStep) {
      result.steps.push(currentStep);
    }

    return result;
  };

  // Validate mission before save
  const validateMission = function () {
    let errors = [];

    if (!yaml || yaml.trim().length === 0) {
      errors.push('Mission YAML is empty');
    } else {
      const parsed = parseYaml(yaml);
      if (!parsed.name || parsed.name.trim().length === 0) {
        errors.push('Mission name is required');
      }
      if (parsed.steps.length === 0) {
        errors.push('At least one step is required');
      } else {
        parsed.steps.forEach((step, si) => {
          if (!step.key || step.key.trim().length === 0) {
            errors.push('Step ' + (si + 1) + ' must have a key');
          }
          if (!step.name || step.name.trim().length === 0) {
            errors.push('Step ' + (si + 1) + ' must have a name');
          }
          if ((step.tool_refs || []).length === 0) {
            errors.push('Step ' + (si + 1) + ' must have at least one tool_ref');
          }
        });
      }
    }

    if (errors.length > 0) {
      setValidationError(errors.join('; '));
      return false;
    }
    setValidationError('');
    return true;
  };

  const missionToYaml = function (m) {
    const parts = [];
    parts.push('name: "' + (m.name || '') + '"');
    parts.push('goal: "' + (m.goal || '') + '"');
    if (m.steps && m.steps.length > 0) {
      parts.push('steps:');
      m.steps.forEach(function (s, i) {
        parts.push('  - key: "' + (s.step_key || s.key || '') + '"');
        parts.push('    name: "' + (s.name || '') + '"');
        if (s.prompt_template) {
          parts.push('    prompt_template: "' + s.prompt_template + '"');
        }
        if (s.agent_key) {
          parts.push('    agent_key: "' + s.agent_key + '"');
        }
        if (s.tool_refs && s.tool_refs.length > 0) {
          parts.push('    tool_refs: ' + JSON.stringify(s.tool_refs));
        }
        if (s.approval_required) {
          parts.push('    approval_required: true');
        }
        if (s.max_retries && s.max_retries !== 3) {
          parts.push('    max_retries: ' + s.max_retries);
        }
        if (s.timeout_seconds && s.timeout_seconds !== 300) {
          parts.push('    timeout_seconds: ' + s.timeout_seconds);
        }
      });
    }
    return parts.join('\n');
  };

  const handleSave = async () => {
    if (!validateMission()) return;
    setLoading(true);
    setError(null);
    setSuccessMessage(null);
    setValidationError('');

    const parsed = parseYaml(yaml);
    const updateData = {
      name: parsed.name,
      goal: parsed.goal || '',
      steps: parsed.steps
    };

    try {
      const res = await fetch('/missions/' + mission.id, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(updateData),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || 'Failed to save mission');
      }
      window.alert('Mission saved successfully (new version created)');
      setSuccessMessage('Mission saved successfully (new version created)');
      fetchMission();
    } catch (err) {
      setError(err.message);
      window.alert('Error saving mission: ' + err.message);
    }
    setLoading(false);
  };

  const handlePublish = async () => {
    if (!mission) return;
    if (mission.state === 'published') {
      window.alert('This mission is already published.');
      return;
    }
    if (!confirmAction('Publish mission "' + mission.name + '"? This will lock edits and make it available for runs.')) return;
    try {
      const res = await fetch('/missions/' + mission.id + '/publish', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to publish mission');
      setSuccessMessage('Mission published successfully!');
      setIsPublished(true);
      fetchMission();
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  const handleClone = async () => {
    if (!mission) return;
    if (!confirmAction('Clone mission "' + mission.name + '"? This creates an independent copy with state=draft, version=1.')) return;
    try {
      const res = await fetch('/missions/' + mission.id + '/clone', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to clone mission');
      setSuccessMessage('Mission cloned successfully!');
      fetchMissions();
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  const handleDelete = async () => {
    if (!mission) return;
    if (!confirmAction('Delete mission "' + mission.name + '"? This cannot be undone.')) return;
    try {
      const res = await fetch('/missions/' + mission.id, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      });
      if (!res.ok) throw new Error('Failed to delete mission');
      setSuccessMessage('Mission deleted successfully.');
      fetchMissions();
    } catch (err) {
      setError(err.message);
    }
    setLoading(false);
  };

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Mission</title>
        </Head>
        <p>Loading mission...</p>
      </div>
    );
  }

  if (!mission) {
    return (
      <div style={{ minHeight: '100vh', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Mission</title>
        </Head>
        <p style={{ color: '#ef4444' }}>{error || 'Mission not found'}</p>
      </div>
    );
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f172a', color: '#e2e8f0' }}>
      <Head>
        <title>MERIDIAN - Mission: {mission.name}</title>
        <meta name="description" content={`Edit mission: ${mission.name}`} />
      </Head>

      <main style={{ maxWidth: '1200px', margin: '0 auto', padding: '2rem' }}>
        <Head>
          <title>MERIDIAN - Mission: {mission.name}</title>
        </Head>

        {successMessage && (
          <div style={{
            backgroundColor: '#059669',
            color: '#d1f7dd',
            padding: '1rem',
            borderRadius: '8px',
            marginBottom: '1.5rem',
            border: '1px solid #059669',
          }}>
            {successMessage}
          </div>
        )}

        {validationError && (
          <div style={{
            backgroundColor: '#dc2626',
            color: '#f87171',
            padding: '1rem',
            borderRadius: '8px',
            marginBottom: '1.5rem',
            border: '1px solid #dc2626',
          }}>
            {validationError}
          </div>
        )}

        <div style={{ marginBottom: '2rem' }}>
          <h1 style={{ fontSize: '1.5rem', fontWeight: '700', margin: '0 0 1rem', background: 'linear-gradient(135deg, #60a5fa, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Mission: {mission.name}
          </h1>
          <p style={{ color: '#64748b', margin: '0.5rem 0' }}>
            Goal: {mission.goal || '—'}
          </p>
          <p style={{ color: '#64748b', margin: '0.5rem 0' }}>
            State: <span style={{ color: '#10b981', fontWeight: '500' }}>{mission.state || 'draft'}</span>
            {isPublished && <span style={{ marginLeft: '1rem', backgroundColor: '#f59e0b', color: '#92400e', padding: '0.2rem 0.5rem', borderRadius: '4px', fontSize: '0.75rem' }}>Published</span>}
          </p>
        </div>

        <div style={{ marginBottom: '2rem', backgroundColor: '#1e293b', borderRadius: '8px', padding: '1.5rem', border: '1px solid #334155' }}>
          <h2 style={{ fontSize: '1.1rem', fontWeight: '600', margin: '0 0 1rem', color: '#f1f5f9' }}>
            Mission YAML (Edit)
          </h2>
          <textarea
            value={yaml}
            onChange={(e) => setYaml(e.target.value)}
            style={{
              width: '100%',
              height: '350px',
              backgroundColor: '#0f172a',
              color: '#e2e8f0',
              border: '1px solid #334155',
              borderRadius: '4px',
              padding: '0.75rem',
              fontFamily: 'monospace',
              fontSize: '0.875rem',
              resize: 'vertical',
              marginBottom: '1rem',
            }}
            rows={25}
            placeholder="Mission YAML will appear here..."
          ></textarea>
          <div style={{ color: '#64748b', fontSize: '0.875rem' }}>
            <strong>Tip:</strong> You can edit the YAML directly. Changes are saved as draft version on each update. The editor validates: mission name, at least one step, and at least one tool per step.
          </div>
        </div>

        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          <button
            onClick={handleSave}
            style={{
              background: 'linear-gradient(135deg, #10b981, #059669)',
              color: 'white',
              padding: '0.75rem 1.5rem',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: '500',
              fontSize: '0.975rem',
            }}
            disabled={validationError ? true : false}
          >
            Save Draft
          </button>
          <span style={{ color: '#64748b', fontSize: '0.875rem', margin: '0 1rem' }}>
            {validationError ? '• ' + validationError : ''}
          </span>
          <button
            onClick={handlePublish}
            style={{
              background: 'linear-gradient(135deg, #f59e0b, #d97706)',
              color: 'white',
              padding: '0.75rem 1.5rem',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: '500',
              fontSize: '0.975rem',
            }}
            disabled={isPublished}
          >
            {isPublished ? 'Published' : 'Publish'}
          </button>
          <button
            onClick={handleClone}
            style={{
              background: 'linear-gradient(135deg, #8b5cf6, #7c3aed)',
              color: 'white',
              padding: '0.75rem 1.5rem',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: '500',
              fontSize: '0.975rem',
            }}
          >
            Clone
          </button>
          <button
            onClick={handleDelete}
            style={{
              background: 'linear-gradient(135deg, #ef4444, #dc2626)',
              color: 'white',
              padding: '0.75rem 1.5rem',
              borderRadius: '8px',
              border: 'none',
              cursor: 'pointer',
              fontWeight: '500',
              fontSize: '0.975rem',
            }}
          >
            Delete
          </button>
        </div>
      </main>
    </div>
  );
}