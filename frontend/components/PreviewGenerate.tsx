"use client";

import { useState } from "react";
import { CheckIcon, ProhibitIcon } from "@phosphor-icons/react";
import { api } from "@/lib/api";

// Authenticated download: fetch with the bearer token, then save the blob.
async function saveDownload(analysisId: string, kind: "jmx" | "manifest") {
  try {
    const { blob, filename } = await api.download(analysisId, kind);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    window.alert(e instanceof Error ? e.message : String(e));
  }
}

export function PreviewGenerate({ analysisId, ruleCount }: { analysisId: string; ruleCount: number }) {
  const [opts, setOpts] = useState({
    num_threads: 1,
    loops: 1,
    parameterize_host: true,
    include_cache_manager: true,
    include_static_secrets: false,
    keep_user_agent: false,
  });
  const [result, setResult] = useState<any>(null);
  const [generated, setGenerated] = useState<any>(null);
  const [validation, setValidation] = useState<any>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function doPreview() {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.preview(analysisId, opts));
      setGenerated(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function doGenerate() {
    setBusy(true);
    setError(null);
    try {
      setGenerated(await api.generate(analysisId, opts));
      setValidation(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function doValidate() {
    setBusy(true);
    setError(null);
    try {
      setValidation(await api.validate(analysisId, secrets));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-4">
      <h3 className="mb-3 text-sm font-semibold">Preview & generate ({ruleCount} rules)</h3>
      <div className="flex flex-wrap gap-4 text-xs">
        <label className="flex items-center gap-1">
          threads
          <input
            type="number"
            min={1}
            value={opts.num_threads}
            onChange={(e) => setOpts({ ...opts, num_threads: +e.target.value })}
            className="w-16 rounded-[10px] bg-surface2 px-2 py-1"
          />
        </label>
        <label className="flex items-center gap-1">
          loops
          <input
            type="number"
            min={1}
            value={opts.loops}
            onChange={(e) => setOpts({ ...opts, loops: +e.target.value })}
            className="w-16 rounded-[10px] bg-surface2 px-2 py-1"
          />
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={opts.parameterize_host}
            onChange={(e) => setOpts({ ...opts, parameterize_host: e.target.checked })}
          />
          parameterize host
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={opts.include_cache_manager}
            onChange={(e) => setOpts({ ...opts, include_cache_manager: e.target.checked })}
          />
          cache manager
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={opts.keep_user_agent}
            onChange={(e) => setOpts({ ...opts, keep_user_agent: e.target.checked })}
          />
          keep User-Agent
        </label>
        <label className="flex items-center gap-1 text-warn">
          <input
            type="checkbox"
            checked={opts.include_static_secrets}
            onChange={(e) => setOpts({ ...opts, include_static_secrets: e.target.checked })}
          />
          embed static secrets (opt-in)
        </label>
      </div>

      <div className="mt-3 flex gap-2">
        <button className="btn-ghost" onClick={doPreview} disabled={busy}>
          Preview Draft
        </button>
        <button className="btn" onClick={doGenerate} disabled={busy}>
          Generate JMX
        </button>
      </div>

      {error && <p className="mt-3 rounded-[10px] bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}

      {result && (
        <div className="mt-4 space-y-2 text-xs">
          <div className="inline-block rounded-[10px] bg-surface2 px-2 py-0.5 font-semibold text-fg">
            Draft JMX: preview only, not saved or executed
          </div>
          <div className={`flex items-center gap-1 ${result.blocked ? "text-danger" : "text-ok"}`}>
            {result.blocked ? (
              <>
                <ProhibitIcon size={14} aria-hidden />
                Blockers present: do not treat as auto-correlated.
              </>
            ) : (
              <>
                <CheckIcon size={14} aria-hidden />
                No blockers.
              </>
            )}
          </div>
          <h4 className="font-semibold text-fg">Variable dependency flow</h4>
          {result.dependency_flow.map((d: any, i: number) => (
            <div key={i} className="rounded-[10px] bg-surface2 p-2">
              <span className="mono text-accent-soft-ink">${`{${d.variable}}`}</span> ←{" "}
              <span className="text-fg-muted">
                {d.producer.request} [{d.producer.location}] via {d.producer.extractor}
              </span>
              <div className="mt-1 text-fg-muted">
                → {d.consumers.map((c: any) => `${c.request} [${c.location}]`).join(", ")}
              </div>
            </div>
          ))}
        </div>
      )}

      {generated && (
        <div className="mt-4 space-y-2 text-xs">
          <div className="inline-block rounded-[10px] bg-warn-soft px-2 py-0.5 font-semibold text-warn">
            Generated JMX: structurally valid, not yet executed by JMeter
          </div>
          <div className={generated.validation.ok ? "text-ok" : "text-danger"}>
            Structural check: {generated.validation.ok ? "passed" : "failed"}
          </div>
          <p className="text-fg-subtle">{generated.note}</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {Object.entries(generated.manifest.summary)
              .filter(([, v]) => typeof v === "number")
              .map(([k, v]) => (
                <div key={k} className="rounded-[10px] bg-surface2 p-2">
                  <div className="text-lg font-semibold text-accent-soft-ink">{v as number}</div>
                  <div className="text-fg-muted">{k.replace(/_/g, " ")}</div>
                </div>
              ))}
          </div>
          <p className="text-fg-subtle">
            {generated.manifest.summary.estimate_basis}
          </p>
          {generated.manifest.collection === "Local Webhook Correlation Demo" && (
            <div className="rounded-[10px] bg-warn-soft p-2 text-warn">
              <strong>Before validation:</strong> this sample calls the local demo API at
              <span className="mono"> 127.0.0.1:8088</span>. Start it from the project folder with
              <div className="mono mt-1 select-all text-fg">
                .\backend\.venv\Scripts\python.exe scripts\webhook_demo.py --port 8088
              </div>
            </div>
          )}
          <div className="flex gap-2 pt-1">
            <button className="btn" onClick={() => saveDownload(analysisId, "jmx")}>
              Download Generated JMX
            </button>
            <button className="btn-ghost" onClick={() => saveDownload(analysisId, "manifest")}>
              Download manifest
            </button>
          </div>

          {/* Validate with real JMeter 5.6.3 */}
          <div className="mt-4 border-t border-line pt-3">
            <h4 className="font-semibold text-fg">Validate with Apache JMeter 5.6.3</h4>
            <p className="mt-1 text-fg-subtle">
              Runs the plan (<span className="mono">jmeter -n -t ... -l ...</span>). Only after it executes
              successfully is this a <span className="text-ok">Validated JMX</span>.
            </p>
            {(generated.manifest.secret_handling?.required_properties || []).length > 0 && (
              <div className="mt-2 space-y-1">
                <p className="text-fg-muted">Supply runtime secrets (passed as -Jname=value, never stored):</p>
                {generated.manifest.secret_handling.required_properties.map((p: string) => (
                  <label key={p} className="flex items-center gap-2">
                    <span className="mono w-40 text-fg-muted">{p}</span>
                    <input
                      type="password"
                      value={secrets[p] || ""}
                      onChange={(e) => setSecrets({ ...secrets, [p]: e.target.value })}
                      className="flex-1 rounded-[10px] bg-surface2 px-2 py-1"
                      placeholder={`value for -J${p}`}
                    />
                  </label>
                ))}
              </div>
            )}
            <button className="btn mt-2" onClick={doValidate} disabled={busy}>
              {busy ? "Running JMeter..." : "Validate with JMeter"}
            </button>
          </div>

          {validation && (
            <div className="mt-3 space-y-2">
              <div
                className={`inline-flex items-center gap-1 rounded-[10px] px-2 py-0.5 font-semibold ${
                  validation.status === "validated"
                    ? "bg-ok-soft text-ok"
                    : "bg-danger-soft text-danger"
                }`}
              >
                {validation.status === "validated" ? (
                  <>
                    <CheckIcon size={14} aria-hidden />
                    Validated JMX: JMeter executed the plan successfully
                  </>
                ) : (
                  <>
                    <ProhibitIcon size={14} aria-hidden />
                    Validation failed: see reasons below
                  </>
                )}
              </div>
              {!validation.jmeter_available && (
                <p className="text-warn">JMeter is not available on the server; validation could not run.</p>
              )}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ["samplers", validation.report.samplers_total],
                  ["successful", validation.report.samplers_success],
                  ["failed", validation.report.samplers_failed],
                  ["assertion fails", validation.report.assertion_failures],
                  ["vars extracted", validation.report.variables_extracted.length],
                  ["vars missing", validation.report.variables_missing.length],
                ].map(([k, v]) => (
                  <div key={k as string} className="rounded-[10px] bg-surface2 p-2">
                    <div className="text-lg font-semibold text-accent-soft-ink">{v as number}</div>
                    <div className="text-fg-muted">{k as string}</div>
                  </div>
                ))}
              </div>
              {validation.report.reasons.map((r: string, i: number) => (
                <p key={i} className="text-fg-muted">• {r}</p>
              ))}
              {validation.report.sampler_results?.length > 0 && (
                <div className="space-y-1">
                  {validation.report.sampler_results.map((s: any, i: number) => (
                    <div key={i} className="flex items-center gap-2 rounded-[10px] bg-surface2 px-2 py-1">
                      <span className={s.success ? "text-ok" : "text-danger"}>
                        {s.success ? <CheckIcon size={14} aria-hidden /> : <ProhibitIcon size={14} aria-hidden />}
                      </span>
                      <span className="mono text-fg">{s.label}</span>
                      <span className="text-fg-subtle">{s.code}</span>
                      {s.assertion_failure && <span className="text-danger">{s.assertion_failure}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
