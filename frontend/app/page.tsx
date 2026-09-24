"use client";

import { useRef, useState } from "react";
import { api, type AnalysisSummary } from "@/lib/api";
import { Uploader } from "@/components/Uploader";
import { HealthPanel } from "@/components/HealthPanel";
import { Explorer } from "@/components/Explorer";
import { CandidateTable } from "@/components/CandidateTable";
import { InsightsPanel } from "@/components/InsightsPanel";
import { DependencyGraph } from "@/components/DependencyGraph";
import { ManualRuleForm } from "@/components/ManualRuleForm";
import { PreviewGenerate } from "@/components/PreviewGenerate";
import { HelpNote } from "@/components/HelpNote";

type Tab = "health" | "explorer" | "candidates" | "classification" | "graph" | "manual" | "generate";

export default function Page() {
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [tab, setTab] = useState<Tab>("health");
  const [ruleCount, setRuleCount] = useState(0);
  const [autoMsg, setAutoMsg] = useState<string | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const autoSubmitted = useRef(false);
  const currentAnalysis = useRef<string | null>(null);

  async function autoCorrelateAll() {
    if (!summary || autoSubmitted.current || summary.auto_correlation_status === "completed") return;
    autoSubmitted.current = true;
    const analysisId = summary.analysis_id;
    setAutoBusy(true);
    setAutoMsg(null);
    try {
      const r = await api.autoCorrelate(analysisId);
      if (currentAnalysis.current !== analysisId) return;
      setSummary((current) => current ? { ...current, auto_correlation_status: "completed" } : current);
      setAutoMsg(
        r.total > 0
          ? `Correlation completed: ${r.total} variable(s) — ${r.variables.join(", ")}. Open Generate to build the JMX.`
          : "No unambiguous response-to-request dependencies were found. Review placeholders and conflicting values under Add rule.",
      );
      await refresh();
      if (currentAnalysis.current === analysisId) setTab("graph");
    } catch (e) {
      if (currentAnalysis.current !== analysisId) return;
      autoSubmitted.current = false;
      setAutoMsg(e instanceof Error ? e.message : String(e));
    } finally {
      if (currentAnalysis.current === analysisId) setAutoBusy(false);
    }
  }

  async function refresh() {
    if (!summary) return;
    const s = await api.getAnalysis(summary.analysis_id);
    if (currentAnalysis.current !== s.analysis_id) return;
    setSummary(s);
    setRuleCount(s.rule_count);
  }

  if (!summary) {
    return <Uploader onDone={(s) => {
      currentAnalysis.current = s.analysis_id;
      autoSubmitted.current = false;
      setAutoBusy(false); setAutoMsg(null); setTab("health");
      setSummary(s); setRuleCount(s.rule_count);
    }} />;
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: "health", label: "Run health" },
    { id: "explorer", label: "Explorer" },
    { id: "candidates", label: `Candidates (${summary.candidate_count})` },
    { id: "classification", label: `Classification (${summary.insight_count})` },
    { id: "graph", label: "Dependency graph" },
    { id: "manual", label: "Add rule" },
    { id: "generate", label: `Generate (${ruleCount})` },
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold">{summary.collection_name || "Analysis"}</h1>
          <p className="text-xs text-slate-400">
            mode: {summary.mode} · analysis <span className="mono">{summary.analysis_id.slice(0, 8)}…</span>
          </p>
        </div>
        <button
          className="btn-ghost text-xs"
          onClick={() => { currentAnalysis.current = null; autoSubmitted.current = false; setAutoBusy(false); setAutoMsg(null); setSummary(null); }}
        >
          Start new analysis
        </button>
      </div>

      <nav className="flex flex-wrap gap-1 border-b border-edge">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`rounded-t px-3 py-2 text-sm ${
              tab === t.id ? "border-b-2 border-brand text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>
      {autoMsg && <p role="status" className="rounded bg-ink/50 px-3 py-2 text-xs text-slate-200">{autoMsg}</p>}

      {tab === "health" && (
        <>
          <HelpNote
            title="What this page tells you"
            steps={[
              "It checks whether your two runs are good enough for automatic correlation.",
              "Green 'ok' = healthy run. Red 'blocker' = not usable automatically (for example too many 401/403 auth failures).",
              "For best results both runs should be mostly 200 (successful).",
            ]}
            tip="If you see a red blocker, log in again to get fresh valid auth, re-run the collection twice so both runs succeed, then Start new analysis and re-upload."
          />
          <HealthPanel summary={summary} />
        </>
      )}
      {tab === "explorer" && (
        <>
          <HelpNote
            title="Browse every recorded request"
            steps={[
              "Requests are listed in the order they ran; click one to inspect it.",
              "Response tab = what the server sent back — this is where dynamic values are PRODUCED (tokens, ids).",
              "Request tab = what you sent — this is where values are REUSED (headers, path, query, body).",
            ]}
            tip="Use this to find a value you want to correlate, then create a rule under 'Add rule'. Secrets are masked by default."
          />
          <Explorer analysisId={summary.analysis_id} />
        </>
      )}
      {tab === "candidates" && (
        <>
          <HelpNote
            title="Dynamic values found automatically"
            steps={[
              "Each row is a value that CHANGED between your two runs and is reused later — a likely correlation.",
              "Click 'Evidence' to see why it was suggested and its confidence score.",
              "'Accept' turns it into a rule; 'Reject' ignores it; 'Accept all high' accepts the safe high-confidence ones at once.",
            ]}
            tip="Cookies like __cf_bm are Cloudflare/session cookies that JMeter's Cookie Manager already handles — you can safely Reject them."
          />
          <div className="rounded-lg border border-brand/40 bg-brand/5 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold">⚡ Auto-correlate everything</h3>
                <p className="text-xs text-slate-400">
                  Scans every response and, wherever a value is reused in a later request, creates the
                  correlation automatically (extractor + variable + substitution) — no manual rules needed.
                </p>
              </div>
              <button
                className="btn"
                onClick={autoCorrelateAll}
                disabled={autoBusy || autoSubmitted.current || summary.auto_correlation_status === "running" || summary.auto_correlation_status === "completed"}
              >
                {autoBusy
                  ? "Correlating…"
                  : summary.auto_correlation_status === "completed"
                    ? "✓ Correlation completed"
                    : "Auto-correlate all reused values"}
              </button>
            </div>
          </div>
          <CandidateTable analysisId={summary.analysis_id} onRulesChanged={refresh} />
        </>
      )}
      {tab === "classification" && (
        <>
          <HelpNote
            title="Every value, classified by evidence"
            steps={[
              "Correlations get a JMeter extractor — but ONLY when an earlier response is proven to produce a value a later request consumes.",
              "Parameterizations changed across runs with no producer (user input) → User Defined Variables / CSV.",
              "External credentials are sensitive and supplied at runtime via ${__P(name,)} — never embedded.",
              "Cookie-managed values are handled by the HTTP Cookie Manager (no extractor). Noise is runtime junk. Review-required needs your judgement.",
            ]}
            tip="A changed value alone is never a correlation. If producer→consumer can't be proven, it is classified here instead of guessed."
          />
          <InsightsPanel analysisId={summary.analysis_id} />
        </>
      )}
      {tab === "graph" && (
        <>
          <HelpNote
            title="See how every value flows between APIs"
            steps={[
              "Each box is an API (in run order). A blue ▲ marks values it PRODUCES; a purple ▼ marks values it CONSUMES.",
              "Each arrow is a correlation: it points from the producer response to the later request that reuses it, labelled with the ${variable} and coloured by confidence.",
              "Click a node to highlight its upstream producers and downstream consumers; click an arrow to see the full correlation (extractor path, location, evidence).",
            ]}
            tip="Use Variable / Confidence / Location / Folder filters and Focus to isolate one thread when the graph gets busy. Drag to pan, scroll to zoom, Fit to recentre."
          />
          <DependencyGraph analysisId={summary.analysis_id} />
        </>
      )}
      {tab === "manual" && (
        <>
          <HelpNote
            title="Correlate a value in a few clicks"
            steps={[
              "Pick the Producer request and click the response value you want to capture (JSON body, XML, or a response header).",
              "The tool automatically searches every later request for that value and lists where it's used — click 'Auto-correlate' to wire the extractor + ${variable} into all of them at once.",
              "Or do it by hand: pick the Consumer request and click the exact spot, then 'Create rule'.",
            ]}
            tip="Auto-correlate handles wrappers like 'Bearer <token>' and works for response-header values (e.g. x-csrf-token) too. If a value isn't reused later, the tool tells you it's a parameter/credential, not a correlation — instead of guessing."
          />
          <ManualRuleForm analysisId={summary.analysis_id} onCreated={refresh} />
        </>
      )}
      {tab === "generate" && (
        <>
          <HelpNote
            title="Build the JMeter test plan"
            steps={[
              "Optionally adjust threads / loops and options.",
              "Click 'Preview Draft' to see the producer → variable → consumer flow before building.",
              "Click 'Generate JMX' — this produces a Generated JMX (structurally valid). It is not 'Validated' until JMeter 5.6.3 actually runs it.",
              "Download the Generated JMX and open it in JMeter 5.6.3.",
            ]}
            tip={
              "External credentials such as auth tokens become ${__P(name,)} in the plan — pass their real values at runtime with jmeter -Jname=<secret>. No secret is embedded."
            }
          />
          <PreviewGenerate analysisId={summary.analysis_id} ruleCount={ruleCount} />
        </>
      )}
    </div>
  );
}
