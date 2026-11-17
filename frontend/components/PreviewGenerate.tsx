"use client";

import { useState } from "react";
import { ArrowDownIcon, CheckIcon, FileCodeIcon, KeyIcon, ProhibitIcon } from "@phosphor-icons/react";
import { api } from "@/lib/api";

// Authenticated download: fetch with the bearer token, then save the blob.
async function saveDownload(analysisId: string, kind: "jmx" | "manifest") {
    const { blob, filename } = await api.download(analysisId, kind);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
}

export function PreviewGenerate({
  analysisId, ruleCount, browserRecording = false,
}: {
  analysisId: string;
  ruleCount: number;
  browserRecording?: boolean;
}) {
  const [opts, setOpts] = useState({
    num_threads: 1,
    loops: 1,
    parameterize_host: true,
    include_cache_manager: true,
    include_static_secrets: false,
    keep_user_agent: browserRecording, // sites behind a bot filter can refuse JMeter's own
  });
  const [result, setResult] = useState<any>(null);
  const [generated, setGenerated] = useState<any>(null);
  const [validation, setValidation] = useState<any>(null);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [activity, setActivity] = useState<"preview" | "generate" | "validate" | "jmx" | "manifest" | null>(null);
  const busy = activity !== null;
  const [error, setError] = useState<string | null>(null);
  const requiredProperties: string[] = generated?.manifest.secret_handling?.required_properties || [];
  const missingSecrets = requiredProperties.filter((name) => !secrets[name]?.trim());
  const validCounts = [opts.num_threads, opts.loops].every((n) => Number.isInteger(n) && n >= 1);

  function changeOptions(next: typeof opts) {
    setOpts(next);
    setGenerated(null);
    setResult(null);
    setValidation(null);
    setSecrets({});
  }

  async function doPreview() {
    if (busy || !validCounts) return;
    setActivity("preview");
    setError(null);
    try {
      setResult(await api.preview(analysisId, opts));
      setGenerated(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivity(null);
    }
  }
  async function doGenerate() {
    if (busy || !validCounts) return;
    setActivity("generate");
    setError(null);
    try {
      setGenerated(await api.generate(analysisId, opts));
      setValidation(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivity(null);
    }
  }
  async function doValidate() {
    if (busy || missingSecrets.length) return;
    setActivity("validate");
    setError(null);
    try {
      setValidation(await api.validate(analysisId, secrets));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSecrets({});
      setActivity(null);
    }
  }

  async function download(kind: "jmx" | "manifest") {
    if (busy) return;
    setActivity(kind);
    setError(null);
    try {
      await saveDownload(analysisId, kind);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setActivity(null);
    }
  }

  return (
    <div className="space-y-6 py-5">
      <div className="flex items-start gap-3">
        <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent-soft text-accent-soft-ink"><FileCodeIcon size={23} aria-hidden /></span>
        <div>
          <h3 className="text-xl font-semibold tracking-tight">Build your JMeter plan</h3>
          <p className="mt-1 text-sm text-fg-muted">{ruleCount} correlation {ruleCount === 1 ? "rule" : "rules"} ready to export. Start with one user and one iteration.</p>
        </div>
      </div>
      <fieldset disabled={busy} className="grid grid-cols-1 gap-4 rounded-xl border border-line bg-surface2/60 p-4 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <legend className="px-2 text-xs font-semibold text-fg-muted">Run settings</legend>
        <label className="flex items-center gap-1">
          threads
          <input
            type="number"
            min={1}
            value={opts.num_threads}
            onChange={(e) => changeOptions({ ...opts, num_threads: +e.target.value })}
            aria-invalid={!Number.isInteger(opts.num_threads) || opts.num_threads < 1}
            className="input ml-auto w-20 border border-line bg-surface px-3 py-2"
          />
        </label>
        <label className="flex items-center gap-1">
          loops
          <input
            type="number"
            min={1}
            value={opts.loops}
            onChange={(e) => changeOptions({ ...opts, loops: +e.target.value })}
            aria-invalid={!Number.isInteger(opts.loops) || opts.loops < 1}
            className="input ml-auto w-20 border border-line bg-surface px-3 py-2"
          />
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={opts.parameterize_host}
            onChange={(e) => changeOptions({ ...opts, parameterize_host: e.target.checked })}
          />
          parameterize host
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={opts.include_cache_manager}
            onChange={(e) => changeOptions({ ...opts, include_cache_manager: e.target.checked })}
          />
          cache manager
        </label>
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={opts.keep_user_agent}
            onChange={(e) => changeOptions({ ...opts, keep_user_agent: e.target.checked })}
          />
          keep User-Agent
        </label>
        <label className="flex items-center gap-1 text-warn">
          <input
            type="checkbox"
            checked={opts.include_static_secrets}
            onChange={(e) => changeOptions({ ...opts, include_static_secrets: e.target.checked })}
          />
          embed static secrets (opt-in)
        </label>
      </fieldset>
      {!validCounts && <p role="alert" className="text-sm text-danger">Threads and loops must be whole numbers of at least 1.</p>}

      <div className="flex flex-wrap gap-2">
        <button className="btn-secondary" onClick={doPreview} disabled={busy || !validCounts}>
          {activity === "preview" ? "Preparing preview..." : "Preview Draft"}
        </button>
        <button className="btn" onClick={doGenerate} disabled={busy || !validCounts}>
          {activity === "generate" ? "Generating JMX..." : "Generate JMX"}
        </button>
      </div>

      {error && <p role="alert" className="rounded-xl bg-danger-soft px-4 py-3 text-sm text-danger">{error}</p>}

      {result && (
        <div className="space-y-3 rounded-xl border border-line p-4 text-sm">
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
        <div className="space-y-5 border-t border-line pt-6 text-sm">
          <div className="inline-block rounded-[10px] bg-warn-soft px-2 py-0.5 font-semibold text-warn">
            Generated JMX: structurally valid, not yet executed by JMeter
          </div>
          <div className={generated.validation.ok ? "text-ok" : "text-danger"}>
            Structural check: {generated.validation.ok ? "passed" : "failed"}
          </div>
          <p className="text-fg-subtle">{generated.note}</p>
          <div className="grid gap-3 sm:grid-cols-3">
            {[
              ["Requests in plan", generated.manifest.summary.matched_requests, "HTTP requests JMeter will replay"],
              ["Correlated values", generated.manifest.summary.confirmed_correlations ?? ruleCount, "Values captured from earlier responses"],
              ["Replacements", generated.manifest.summary.downstream_values_replaced, "Places that reuse those values"],
            ].map(([label, count, hint]) => (
              <div key={label as string} className="rounded-xl border border-line bg-surface2/60 px-4 py-4">
                <div className="text-3xl font-semibold tabular-nums tracking-tight text-fg">{typeof count === "number" ? count : "Not available"}</div>
                <div className="mt-2 font-medium text-fg">{label}</div>
                <div className="mt-1 text-xs leading-relaxed text-fg-muted">{hint}</div>
              </div>
            ))}
          </div>
          <p className="text-sm text-fg-muted">A correlation is a reused value, not a request. One correlated value can be used by many requests.</p>
          {generated.manifest.collection === "Local Webhook Correlation Demo" && (
            <div className="rounded-[10px] bg-warn-soft p-2 text-warn">
              <strong>Before validation:</strong> this sample calls the local demo API at
              <span className="mono"> 127.0.0.1:8088</span>. Start it from the project folder with
              <div className="mono mt-1 select-all text-fg">
                .\backend\.venv\Scripts\python.exe scripts\webhook_demo.py --port 8088
              </div>
            </div>
          )}
          {requiredProperties.length > 0 && (
            <section className="rounded-xl border border-accent/25 bg-accent-soft/40 p-4 sm:p-5" aria-labelledby="jmeter-setup-heading">
              <div className="flex items-center gap-2 text-accent-soft-ink">
                <KeyIcon size={19} aria-hidden />
                <h4 id="jmeter-setup-heading" className="font-semibold">Before running in JMeter</h4>
              </div>
              <p className="mt-2 text-sm leading-relaxed text-fg-muted">Your downloaded plan needs {requiredProperties.length === 1 ? "a runtime value" : "runtime values"}. Without them, JMeter can stop before sending the login request.</p>
              {browserRecording ? <ol className="mt-4 space-y-2 text-sm leading-relaxed text-fg">
                <li>1. Open the downloaded JMX and select <strong>User Defined Variables</strong>.</li>
                <li>2. Find each row below and replace its entire value with your actual value, without quotes or <code className="mono">{"${...}"}</code>.</li>
                <li>3. Click another cell to apply the edit. Clear old results, then start the test.</li>
              </ol> : <p className="mt-4 text-sm leading-relaxed text-fg">Supply each required value as a JMeter property when launching your plan, using <code className="mono">-Jname=value</code> with the property names below.</p>}
              <div className="mt-4 divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface">
                {requiredProperties.map((name) => (
                  <div key={name} className="flex flex-wrap items-center justify-between gap-2 px-3 py-3">
                    <span className="mono font-medium">{name}</span>
                    <span className="text-xs text-fg-muted">{browserRecording ? "Replace " : "Runtime property "}<code className="mono break-all">{browserRecording ? `\${__P(${name},)}` : `-J${name}=value`}</code></span>
                  </div>
                ))}
              </div>
              {browserRecording && requiredProperties.includes("Code") && <p className="mt-3 text-sm font-medium text-fg">Code is your login password for this recording. Enter only the password itself.</p>}
              <p className="mt-3 text-xs leading-relaxed text-fg-muted">Values entered for validation below are temporary and do not populate the downloaded JMX. Saving credentials inside JMeter stores them in that local file.</p>
            </section>
          )}
          <div className="flex flex-wrap gap-2 pt-1">
            <button className="btn" disabled={busy || !generated.validation.ok} onClick={() => download("jmx")}>
              <ArrowDownIcon size={17} aria-hidden />
              {activity === "jmx" ? "Downloading JMX..." : "Download Generated JMX"}
            </button>
            <button className="btn-ghost" disabled={busy} onClick={() => download("manifest")}>
              {activity === "manifest" ? "Downloading manifest..." : "Download manifest"}
            </button>
          </div>

          {/* Validate with real JMeter 5.6.3 */}
          <div className="rounded-xl border border-line p-4 sm:p-5">
            <h4 className="font-semibold text-fg">Run a validation test</h4>
            <p className="mt-1 text-fg-subtle">
              Runs the plan (<span className="mono">jmeter -n -t ... -l ...</span>). Only after it executes
              successfully is this a <span className="text-ok">Validated JMX</span>.
            </p>
            {(generated.manifest.secret_handling?.required_properties || []).length > 0 && (
              <div className="mt-2 space-y-1">
                <p className="text-fg-muted">Enter each required credential. Values are cleared after validation and supplied through a temporary properties file.</p>
                {requiredProperties.some((p) => p.toLowerCase() === "authorization") && <p className="text-fg-muted">For Authorization, enter the complete header value: Basic followed by the encoded key pair, or Bearer followed by the token. Use the same format as your successful Postman request.</p>}
                {generated.manifest.secret_handling.required_properties.map((p: string) => (
                  <label key={p} className="flex flex-col gap-2 py-2 sm:flex-row sm:items-center">
                    <span className="mono w-40 text-fg-muted">{p}</span>
                    <input
                      type="password"
                      value={secrets[p] || ""}
                      onChange={(e) => setSecrets({ ...secrets, [p]: e.target.value })}
                      disabled={busy}
                      className="input min-w-0 flex-1 border border-line bg-surface2 px-3 py-2"
                      placeholder={`value for -J${p}`}
                    />
                  </label>
                ))}
              </div>
            )}
            {missingSecrets.length > 0 && <p role="status" className="mt-2 text-warn">Required before validation: {missingSecrets.join(", ")}</p>}
            <p className="mt-2 text-fg-muted">Validation sends real requests again. Start with one thread and one loop. Hosted deployments currently require you to download and validate the plan in your own JMeter environment.</p>
            <button className="btn mt-2" onClick={doValidate} disabled={busy || missingSecrets.length > 0}>
              {activity === "validate" ? "Running JMeter..." : "Validate with JMeter"}
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
                    <div key={i} className="flex flex-wrap items-center gap-2 break-all rounded-[10px] bg-surface2 px-3 py-2">
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
