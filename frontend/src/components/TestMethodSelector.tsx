import React, { useCallback, useEffect, useMemo, useState } from 'react';

import QuickTestEditor from './QuickTestEditor';
import { downloadBlob } from '../utils/downloadBlob';

type TestMethod = 'macro' | 'python' | 'quick_test';

type TestDetailResponse = {
  part_number: string;
  method: TestMethod;
  python_folder_rel?: string | null;
  python_folder_abs?: string | null;
  quicktest_rel?: string | null;
  quicktest_abs?: string | null;
  has_folder: boolean;
  has_file: boolean;
  notes?: string | null;
};

type AssignRequest = {
  part_number: string;
  method: TestMethod;
  notes?: string | null;
};

const fetcher = (input: RequestInfo | URL, init?: RequestInit) =>
  (window.api?.fetch ?? fetch)(input, init);

export interface TestMethodSelectorProps {
  pn: string;
  value?: TestMethod;
  disabled?: boolean;
  onMethodChange?: (method: TestMethod) => void;
  isElectron?: boolean;
}

export const TestMethodSelector: React.FC<TestMethodSelectorProps> = ({
  pn,
  value,
  disabled,
  onMethodChange,
  isElectron,
}) => {
  const [method, setMethod] = useState<TestMethod>(value ?? 'macro');
  const [detail, setDetail] = useState<TestDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showQuickTest, setShowQuickTest] = useState(false);
  const effectiveElectron = isElectron ?? Boolean(window.api?.isElectron);

  useEffect(() => {
    if (value && value !== method) {
      setMethod(value);
    }
  }, [value]);

  const detailUrl = useMemo(
    () => `/tests/${encodeURIComponent(pn)}/detail`,
    [pn]
  );

  const assignUrl = useMemo(() => '/tests/assign', []);

  const fetchDetail = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await fetcher(detailUrl);
      if (!resp.ok) {
        if (resp.status === 404) {
          setDetail(null);
        } else {
          throw new Error(`Failed to load test detail (${resp.status})`);
        }
      } else {
        const payload = (await resp.json()) as TestDetailResponse;
        setDetail(payload);
        setMethod(payload.method);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [detailUrl]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const assignMethod = useCallback(
    async (nextMethod: TestMethod) => {
      setLoading(true);
      setError(null);
      const payload: AssignRequest = {
        part_number: pn,
        method: nextMethod,
      };
      try {
        const resp = await fetcher(assignUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (!resp.ok) {
          throw new Error(`Failed to assign test method (${resp.status})`);
        }
        const data = (await resp.json()) as TestDetailResponse;
        setMethod(data.method);
        setDetail(data);
        onMethodChange?.(data.method);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [assignUrl, pn, onMethodChange]
  );

  const handleSelectChange = useCallback(
    (event: React.ChangeEvent<HTMLSelectElement>) => {
      const nextMethod = event.target.value as TestMethod;
      setMethod(nextMethod);
      void assignMethod(nextMethod);
    },
    [assignMethod]
  );

  const handleZipDownload = useCallback(async () => {
    setError(null);
    try {
      const resp = await fetcher(
        `/tests/${encodeURIComponent(pn)}/python/zip`
      );
      if (!resp.ok) {
        throw new Error('Unable to download python assets');
      }
      const blob = await resp.blob();
      downloadBlob(`${pn}_python.zip`, blob, 'application/zip');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [pn]);

  const handleReveal = useCallback(async () => {
    if (!detail?.python_folder_abs) {
      return;
    }
    if (effectiveElectron && window.api?.revealPath) {
      await window.api.revealPath(detail.python_folder_abs);
    }
  }, [detail, effectiveElectron]);

  const handleOpenQuickTest = useCallback(() => {
    setShowQuickTest(true);
  }, []);

  const handleQuickTestSaved = useCallback(() => {
    void fetchDetail();
  }, [fetchDetail]);

  const handleQuickTestDownload = useCallback(async () => {
    setError(null);
    try {
      const resp = await fetcher(
        `/tests/${encodeURIComponent(pn)}/quicktest/read`,
        { method: 'POST' }
      );
      if (!resp.ok) {
        throw new Error('Unable to read Quick Test file');
      }
      const payload = await resp.json();
      const content = payload?.content ?? '';
      downloadBlob(`${pn}.txt`, content, 'text/plain');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [pn]);

  return (
    <div className="test-method-selector">
      <label>
        Test Method:
        <select
          value={method}
          onChange={handleSelectChange}
          disabled={disabled || loading}
        >
          <option value="macro">Macro</option>
          <option value="python">Python</option>
          <option value="quick_test">Quick Test</option>
        </select>
      </label>
      {error ? <div className="test-method-selector__error">{error}</div> : null}
      {detail ? (
        <div className="test-method-selector__detail">
          <div>
            <strong>Test detail</strong>
          </div>
          {detail.notes ? <p>{detail.notes}</p> : null}
          {detail.method === 'python' ? (
            <div className="test-method-selector__python">
              <p>
                Folder: <code>{detail.python_folder_rel ?? 'n/a'}</code>
              </p>
              <div className="test-method-selector__actions">
                {effectiveElectron && detail.python_folder_abs ? (
                  <button type="button" onClick={handleReveal}>
                    Open Folder
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={() => void handleZipDownload()}
                  disabled={!detail.has_folder}
                >
                  Download .zip
                </button>
              </div>
              {!effectiveElectron ? (
                <p>
                  Use the Download button to fetch the folder contents. Files
                  live under <code>{detail.python_folder_rel ?? ''}</code>.
                </p>
              ) : null}
            </div>
          ) : null}
          {detail.method === 'quick_test' ? (
            <div className="test-method-selector__quicktest">
              <p>
                File: <code>{detail.quicktest_rel ?? 'n/a'}</code>
              </p>
              <div className="test-method-selector__actions">
                <button type="button" onClick={handleOpenQuickTest}>
                  Edit Quick Test
                </button>
                <button type="button" onClick={() => void handleQuickTestDownload()}>
                  Save As…
                </button>
              </div>
            </div>
          ) : null}
          {detail.method === 'macro' ? (
            <p>Macro tests use existing prefix macros.</p>
          ) : null}
        </div>
      ) : (
        <p>No test method assigned.</p>
      )}
      <QuickTestEditor
        pn={pn}
        open={showQuickTest}
        onClose={() => setShowQuickTest(false)}
        onSaved={handleQuickTestSaved}
        isElectron={effectiveElectron}
      />
    </div>
  );
};

export default TestMethodSelector;

