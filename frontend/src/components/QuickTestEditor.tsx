import React, { useCallback, useEffect, useMemo, useState } from 'react';

import { downloadBlob } from '../utils/downloadBlob';

type QuickTestReadResponse = {
  content: string;
  created: boolean;
};

type QuickTestWriteResponse = {
  saved: boolean;
  bytes_written: number;
  rel_path: string;
};

const fetcher = (input: RequestInfo | URL, init?: RequestInit) =>
  (window.api?.fetch ?? fetch)(input, init);

export interface QuickTestEditorProps {
  pn: string;
  open: boolean;
  onClose: () => void;
  onSaved?: (content: string) => void;
  isElectron?: boolean;
}

export const QuickTestEditor: React.FC<QuickTestEditorProps> = ({
  pn,
  open,
  onClose,
  onSaved,
  isElectron,
}) => {
  const [value, setValue] = useState('');
  const [initialValue, setInitialValue] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = value !== initialValue;

  const readUrl = useMemo(
    () => `/tests/${encodeURIComponent(pn)}/quicktest/read`,
    [pn]
  );
  const writeUrl = useMemo(
    () => `/tests/${encodeURIComponent(pn)}/quicktest/write`,
    [pn]
  );

  const loadContent = useCallback(async () => {
    if (!open) {
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const resp = await fetcher(readUrl, { method: 'POST' });
      if (!resp.ok) {
        throw new Error(`Quick Test read failed (${resp.status})`);
      }
      const data = (await resp.json()) as QuickTestReadResponse;
      setValue(data.content ?? '');
      setInitialValue(data.content ?? '');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [open, readUrl]);

  useEffect(() => {
    if (open) {
      void loadContent();
    }
  }, [open, loadContent]);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const resp = await fetcher(writeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: value }),
      });
      if (!resp.ok) {
        throw new Error(`Quick Test write failed (${resp.status})`);
      }
      const data = (await resp.json()) as QuickTestWriteResponse;
      if (!data.saved) {
        throw new Error('Quick Test save was rejected by the server');
      }
      setInitialValue(value);
      onSaved?.(value);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }, [value, writeUrl, onSaved]);

  const handleSaveAs = useCallback(async () => {
    if (isElectron && window.api?.saveText) {
      await window.api.saveText(value, `${pn}.txt`);
      return;
    }
    downloadBlob(`${pn}.txt`, value, 'text/plain');
  }, [isElectron, pn, value]);

  if (!open) {
    return null;
  }

  return (
    <div className="quicktest-editor">
      <div className="quicktest-editor__header">
        <strong>Quick Test – {pn}</strong>
        <div className="quicktest-editor__actions">
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      {error ? <div className="quicktest-editor__error">{error}</div> : null}
      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder="Enter Quick Test instructions..."
        disabled={loading}
      />
      <div className="quicktest-editor__footer">
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving}
        >
          Save
        </button>
        <button type="button" onClick={handleSaveAs} disabled={saving}>
          Save As…
        </button>
        {loading ? <span className="quicktest-editor__status">Loading…</span> : null}
        {saving ? <span className="quicktest-editor__status">Saving…</span> : null}
        {!dirty && !saving ? (
          <span className="quicktest-editor__status">All changes saved.</span>
        ) : null}
      </div>
    </div>
  );
};

export default QuickTestEditor;

