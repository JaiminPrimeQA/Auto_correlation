"use client";

import { useState } from "react";
import { ArrowRightIcon, MagnifyingGlassIcon, BrowserIcon, FileCodeIcon, FunnelIcon } from "@phosphor-icons/react";
import { Stepper } from "@/components/ui/Stepper";
import { api, ApiError, type AnalysisSummary, type HarEntry, type HarFileReview } from "@/lib/api";
import { formatBytes } from "@/lib/account";
import { PrivacyNote } from "@/components/PrivacyNote";
import { useAccount } from "@/components/account/AccountProvider";
import { DropZone, PlanAllowance, SignInPrompt } from "./FilesStep";

const HOW = [
  ["Record your flow", "In Chrome DevTools, open Network, use the app, then save everything as a HAR file."],
  ["We clean it", "Scripts, styles, images, fonts and trackers are removed. You check what stays."],
  ["Download your plan", "Values reused between requests are correlated in a JMeter 5.6.3 plan."],
] as const;

function RequestRow({ entry, checked, onToggle, disabled }: { entry: HarEntry; checked: boolean; onToggle: () => void; disabled: boolean }) {
  const failed = entry.reason.startsWith("Failed attempt");
  return (
    <li>
      <label className="request-row">
        <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={checked} onChange={onToggle} disabled={disabled} />
        <span className="min-w-0 flex-1">
          <span className="block break-all text-[13px]">
            <b className="method-label mr-2">{entry.method}</b> {entry.url}
          </span>
          <span className={`block text-xs ${failed ? "text-danger" : "text-fg-subtle"}`}>
            {entry.status} · {entry.reason}
          </span>
        </span>
      </label>
    </li>
  );
}

/** Upload a browser recording, review what is kept, convert it. */
export function HarImport({ onDone }: { onDone: (summary: AnalysisSummary, note: string) => void }) {
  const [recording, setRecording] = useState<File | null>(null);
  const [second, setSecond] = useState<File | null>(null);
  const [review, setReview] = useState<HarFileReview[] | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const { me, refresh } = useAccount();

  const files = [recording, second].filter((f): f is File => f !== null);

  function showError(e: unknown) {
    // Signed out in another tab: show the sign-in card instead of an error.
    if (e instanceof ApiError && e.code === "sign_in_required") void refresh();
    else setError(e instanceof Error ? e.message : String(e));
  }

  async function inspect() {
    if (!recording || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api.inspectHar(files);
      setReview(result.files);
      setSelected(new Set(result.files[0].entries.filter((e) => e.decision === "keep").map((e) => e.index)));
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  async function convert() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const summary = await api.createHarAnalysis(files, [...selected].sort((a, b) => a - b));
      void refresh(); // one free use was taken
      try {
        let note = "Run health blocked correlation, so this plan has none yet. See Run health for the reason.";
        if (!summary.blocked) {
          const { total, variables } = await api.autoCorrelate(summary.analysis_id);
          note = total > 0
            ? `${total} value(s) correlated: ${variables.join(", ")}.`
            : "No value was reused between these requests, so this plan has no correlations.";
        }
        onDone(await api.getAnalysis(summary.analysis_id), note);
      } catch {
        // The analysis exists and its results page can retry: a second Convert
        // here would create another analysis and take another free use.
        onDone(summary, "Correlation did not finish. Run it again from the Candidates tab before generating.");
      }
    } catch (e) {
      showError(e);
    } finally {
      setBusy(false);
    }
  }

  function toggle(index: number) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(index)) next.add(index);
      return next;
    });
  }

  if (review) {
    const [file] = review;
    const matches = (e: HarEntry) => `${e.method} ${e.url} ${e.status} ${e.reason}`.toLowerCase().includes(query.trim().toLowerCase());
    const kept = file.entries.filter((e) => e.decision === "keep" && matches(e));
    const optional = file.entries.filter((e) => e.decision === "optional" && matches(e));
    const warnings = [...new Set(review.flatMap((f) => f.warnings))];
    return (
      <div className="space-y-6">
        <Stepper steps={["Upload", "Review", "Correlate", "Download"]} current={busy ? 2 : 1} />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="eyebrow mb-2">Recording review</p>
            <h2 className="text-2xl font-semibold tracking-tight">Check what goes into your plan</h2>
            <p className="mt-1 text-sm text-fg-muted">
              <span className="break-all">{file.filename}</span> · {formatBytes(file.size_bytes)} uploaded
            </p>
          </div>
          <button className="btn-secondary" disabled={busy} onClick={() => { setReview(null); setError(null); setQuery(""); }}>Back</button>
        </div>
        <div className="flex rounded-xl border border-line bg-surface p-5">
          <div className="review-stat"><strong>{file.total}</strong><span>Recorded requests</span></div>
          <div className="review-stat text-accent-soft-ink"><strong>{selected.size}</strong><span>Selected for replay</span></div>
          <div className="review-stat"><strong>{file.removed}</strong><span>Removed automatically</span></div>
        </div>
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0 overflow-hidden rounded-xl border border-line bg-surface">
          <div className="border-b border-line p-4">
            <label className="relative block"><MagnifyingGlassIcon className="absolute left-3 top-3 text-fg-subtle" size={18} aria-hidden />
              <input type="search" aria-label="Search requests" placeholder="Search URL, method or status…" className="input pl-10" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="text-fg-subtle">{kept.length + optional.length} matching requests · selection includes hidden rows</span>
              <button className="font-medium text-accent-soft-ink underline underline-offset-4" disabled={busy} onClick={() => setSelected(new Set(file.entries.filter((e) => e.decision === "keep").map((e) => e.index)))}>Restore recommended selection</button>
            </div>
          </div>
          <div className="max-h-[600px] overflow-y-auto">
          <section>
            <h3 className="border-b border-line bg-surface2 px-4 py-3 text-xs font-semibold">API calls & session pages ({kept.length})</h3>
            <ul>
              {kept.map((e) => (
                <RequestRow key={e.index} entry={e} checked={selected.has(e.index)} onToggle={() => toggle(e.index)} disabled={busy} />
              ))}
            </ul>
          </section>
          {optional.length > 0 && (
            <section>
              <h3 className="bg-surface2 px-4 pt-3 text-xs font-semibold">Left out unless you tick them ({optional.length})</h3>
              <p className="border-b border-line bg-surface2 px-4 pb-3 pt-1 text-xs leading-relaxed text-fg-muted">
                Third-party services would be load tested too. A failed login replayed under load can lock your test
                account.
              </p>
              <ul>
                {optional.map((e) => (
                  <RequestRow key={e.index} entry={e} checked={selected.has(e.index)} onToggle={() => toggle(e.index)} disabled={busy} />
                ))}
              </ul>
            </section>
          )}
          {kept.length + optional.length === 0 && <p role="status" className="p-8 text-center text-sm text-fg-muted">No matching requests. Try another URL, method or status.</p>}
          </div>
        </div>
        <aside className="space-y-5 lg:sticky lg:top-24">
          <div className="rounded-xl border border-line bg-surface p-5">
            <FunnelIcon size={24} className="mb-3 text-accent-soft-ink" aria-hidden />
            <h3 className="font-semibold">A focused test plan</h3>
            <p className="mt-2 text-sm leading-relaxed text-fg-muted">Assets and tracking calls are removed. Session pages stay when they may supply cookies or tokens.</p>
            <p className="mt-3 border-t border-line pt-3 text-xs text-fg-subtle">Default selection data: {formatBytes(file.cleaned_bytes)}. Final size depends on your selection.</p>
          </div>
          {warnings.length > 0 && <section className="rounded-xl border border-warn/20 bg-warn-soft p-4">
            <h3 className="mb-2 text-sm font-semibold text-warn">Before you continue</h3>
            <ul className="space-y-3 text-xs leading-relaxed text-fg-muted">{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
          </section>}
          <details className="rounded-xl border border-line bg-surface p-4 text-xs text-fg-muted">
            <summary className="cursor-pointer font-medium text-fg">Removed automatically ({file.removed})</summary>
            <ul className="mt-2 space-y-0.5 pl-1">
              {Object.entries(file.removed_by_reason).map(([reason, count]) => (
                <li key={reason}>{reason}: {count}</li>
              ))}
            </ul>
          </details>
        </aside>
        </div>
          {error && <p role="alert" className="rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
          <div className="sticky bottom-3 z-10 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line-strong bg-surface/95 p-4 shadow-lg backdrop-blur">
            <p className="text-sm"><strong className="tabular-nums">{selected.size} requests</strong><span className="ml-2 text-fg-muted">ready to analyze</span></p>
            <button className="btn" disabled={busy || selected.size === 0} onClick={convert}>
              {busy ? "Converting..." : "Convert to correlated JMX"}<ArrowRightIcon size={16} aria-hidden />
            </button>
          </div>
      </div>
    );
  }

  return (
    <div>
    <Stepper steps={["Upload", "Review", "Correlate", "Download"]} current={0} />
    <div className="grid items-start gap-10 lg:grid-cols-[0.9fr_1.1fr]">
      <div className="rise">
        <p className="eyebrow mb-3">From browser to benchmark</p>
        <h2 style={{ "--i": 0 } as React.CSSProperties} className="mb-4 max-w-[18ch] text-[30px] font-semibold leading-[1.15] tracking-[-0.035em]">
          Your API journey, ready for JMeter.
        </h2>
        <p style={{ "--i": 1 } as React.CSSProperties} className="mb-6 max-w-[44ch] text-[15px] leading-relaxed text-fg-muted">
          Upload a HAR file. We keep API calls and necessary session pages, then find the values that pass from one request to the
          next. Nothing is replayed against your site.
        </p>
        <ol style={{ "--i": 2 } as React.CSSProperties} className="space-y-3.5">
          {HOW.map(([title, text], i) => (
            <li key={title} className="flex gap-3 text-sm leading-normal text-fg-muted">
              <span className="grid h-7 w-7 flex-none place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent-soft-ink">{i + 1}</span>
              <span><b className="block font-medium text-fg">{title}</b>{text}</span>
            </li>
          ))}
        </ol>
        <div className="journey-diagram" aria-label="Example correlation flow">
          <p className="border-b border-line bg-surface2 px-4 py-2 text-[10px] font-semibold uppercase tracking-widest text-fg-subtle">How values stay connected · example</p>
          <div className="journey-node"><span><span className="method-label mr-2">POST</span>/auth/login</span><BrowserIcon size={17} aria-hidden /></div>
          <div className="mx-4 border-l-2 border-accent/40 py-3 pl-4"><span className="mono rounded bg-accent-soft px-2 py-1 text-xs text-accent-soft-ink">response.token → {"${token}"}</span></div>
          <div className="journey-node"><span><span className="method-label mr-2">GET</span>/account</span><span className="text-fg-subtle">Reuses fresh token</span></div>
        </div>
      </div>

      {me?.sign_in_required ? <SignInPrompt me={me} /> : (
        <div className="upload-panel rise space-y-5">
          <div className="flex items-start gap-3 border-b border-line pb-4"><FileCodeIcon size={24} className="text-accent-soft-ink" aria-hidden /><div><h3 className="font-semibold">Add your recording</h3><p className="mt-1 text-xs text-fg-muted">HAR, JSON or an extensionless browser export</p></div></div>
          {/* No file-type filter: Chrome can save a HAR with no extension. */}
          <DropZone id="har-file" label="HAR recording" emptyTitle="Choose or drop a recording"
            emptyHint="Choose a file or drop it here" file={recording} onPick={setRecording} disabled={busy} />
          <DropZone id="har-second-file" label="Second recording" optional emptyTitle="Choose or drop a second recording"
            emptyHint="The same flow recorded again proves which values really change" file={second} onPick={setSecond} disabled={busy} />
          {me && <PlanAllowance me={me} />}
          <PrivacyNote />
          {error && <p role="alert" className="rounded-[10px] bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>}
          <div className="border-t border-line pt-4">
            <button className="btn w-full" disabled={!recording || busy} onClick={inspect}>
              {busy ? "Cleaning..." : "Clean recording"}<ArrowRightIcon size={16} aria-hidden />
            </button>
          </div>
        </div>
      )}
    </div>
    </div>
  );
}
