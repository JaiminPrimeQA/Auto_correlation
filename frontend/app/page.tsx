"use client";

import { useRef, useState } from "react";
import { CheckIcon, LightningIcon } from "@phosphor-icons/react";
import { api, type AnalysisSummary } from "@/lib/api";
import { AppHeader } from "@/components/AppHeader";
import { HealthPanel } from "@/components/HealthPanel";
import { Explorer } from "@/components/Explorer";
import { CandidateTable } from "@/components/CandidateTable";
import { InsightsPanel } from "@/components/InsightsPanel";
import { DependencyGraph } from "@/components/DependencyGraph";
import { ManualRuleForm } from "@/components/ManualRuleForm";
import { PreviewGenerate } from "@/components/PreviewGenerate";
import { HelpNote } from "@/components/HelpNote";
import { CollectionWizard } from "@/components/collection/CollectionWizard";
import { ResultsHeader } from "@/components/results/ResultsHeader";
import { NextStepCard } from "@/components/results/NextStepCard";
import { ToastProvider, useToast } from "@/components/ui/Toast";
import { Tabs } from "@/components/ui/Tabs";
import { discardSession } from "@/lib/session";

type Tab = "health" | "explorer" | "candidates" | "classification" | "graph" | "manual" | "generate";

type AutoCorrelateResult = Awaited<ReturnType<typeof api.autoCorrelate>>;

function Results({
  summary, tab, setTab, ruleCount, refresh, autoBusy, autoCorrelateAll, autoMsg,
}: {
  summary: AnalysisSummary;
  tab: Tab;
  setTab: (t: Tab) => void;
  ruleCount: number;
  refresh: () => Promise<void>;
  autoBusy: boolean;
  autoCorrelateAll: () => Promise<AutoCorrelateResult | undefined>;
  autoMsg: string | null;
}) {
  const toast = useToast();

  async function handleAutoCorrelate() {
    const r = await autoCorrelateAll();
    if (r) toast.show(r.total > 0 ? `${r.total} correlations created` : "Nothing to correlate");
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
      <ResultsHeader summary={summary} />
      <NextStepCard summary={summary} busy={autoBusy} onAutoCorrelate={handleAutoCorrelate} onOpenTab={setTab} />
      <div className="card px-4 pb-4 pt-1">
        <Tabs tabs={tabs} active={tab} onChange={setTab} />
        {autoMsg && (
          <p role="status" className="mt-3 rounded-[10px] bg-surface2 px-3 py-2 text-[13px] text-fg">
            {autoMsg}
          </p>
        )}

        {tab === "health" && (
          <>
            <HelpNote
              title="What this page tells you"
              steps={[
                "It checks whether your two runs are good enough for automatic correlation.",
                "Green 'ok' means a healthy run. Red 'blocker' means not usable automatically, for example too many 401/403 auth failures.",
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
                "Response tab is what the server sent back: this is where dynamic values are PRODUCED (tokens, ids).",
                "Request tab is what you sent: this is where values are REUSED (headers, path, query, body).",
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
                "Each row is a value that CHANGED between your two runs and is reused later, a likely correlation.",
                "Click 'Evidence' to see why it was suggested and its confidence score.",
                "'Accept' turns it into a rule; 'Reject' ignores it; 'Accept all high' accepts the safe high-confidence ones at once.",
              ]}
              tip="Cookies like __cf_bm are Cloudflare/session cookies that JMeter's Cookie Manager already handles, so you can safely Reject them."
            />
            <div className="rounded-[10px] border border-accent/30 bg-accent-soft/40 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="inline-flex items-center gap-1.5 text-sm font-semibold">
                    <LightningIcon size={16} aria-hidden />
                    Auto-correlate everything
                  </h3>
                  <p className="text-xs text-fg-muted">
                    Scans every response and, wherever a value is reused in a later request, creates the
                    correlation automatically (extractor, variable and substitution). No manual rules needed.
                  </p>
                </div>
                <button
                  className="btn"
                  onClick={handleAutoCorrelate}
                  disabled={summary.blocked || autoBusy || summary.auto_correlation_status === "running" || summary.auto_correlation_status === "completed"}
                >
                  {autoBusy ? (
                    "Correlating..."
                  ) : summary.auto_correlation_status === "completed" ? (
                    <span className="inline-flex items-center gap-1.5">
                      <CheckIcon size={16} aria-hidden />
                      Correlation completed
                    </span>
                  ) : (
                    "Auto-correlate all reused values"
                  )}
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
                "Correlations get a JMeter extractor, but ONLY when an earlier response is proven to produce a value a later request consumes.",
                "Parameterizations changed across runs with no producer (user input) go to User Defined Variables / CSV.",
                "External credentials are sensitive and supplied at runtime via ${__P(name,)}, never embedded.",
                "Cookie-managed values are handled by the HTTP Cookie Manager (no extractor). Noise is runtime junk. Review-required needs your judgement.",
              ]}
              tip="A changed value alone is never a correlation. If producer to consumer can't be proven, it is classified here instead of guessed."
            />
            <InsightsPanel analysisId={summary.analysis_id} />
          </>
        )}
        {tab === "graph" && (
          <>
            <HelpNote
              title="See how every value flows between APIs"
              steps={[
                "Each box is an API (in run order). A blue triangle marks values it PRODUCES; a purple triangle marks values it CONSUMES.",
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
                "The tool automatically searches every later request for that value and lists where it's used; click 'Auto-correlate' to wire the extractor, variable and substitution into all of them at once.",
                "Or do it by hand: pick the Consumer request and click the exact spot, then 'Create rule'.",
              ]}
              tip="Auto-correlate handles wrappers like 'Bearer <token>' and works for response-header values (e.g. x-csrf-token) too. If a value isn't reused later, the tool tells you it's a parameter/credential, not a correlation, instead of guessing."
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
                "Click 'Preview Draft' to see the producer, variable and consumer flow before building.",
                "Click 'Generate JMX': this produces a Generated JMX (structurally valid). It is not 'Validated' until JMeter 5.6.3 actually runs it.",
                "Download the Generated JMX and open it in JMeter 5.6.3.",
              ]}
              tip={
                "External credentials such as auth tokens become ${__P(name,)} in the plan; pass their real values at runtime with jmeter -Jname=<secret>. No secret is embedded."
              }
            />
            <PreviewGenerate analysisId={summary.analysis_id} ruleCount={ruleCount} />
          </>
        )}
      </div>
    </div>
  );
}

export default function Page() {
  const [summary, setSummary] = useState<AnalysisSummary | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [wizardKey, setWizardKey] = useState(0);
  const [tab, setTab] = useState<Tab>("health");
  const [ruleCount, setRuleCount] = useState(0);
  const [autoMsg, setAutoMsg] = useState<string | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);
  const autoSubmitted = useRef(false);
  const currentAnalysis = useRef<string | null>(null);

  async function autoCorrelateAll(): Promise<AutoCorrelateResult | undefined> {
    if (!summary || autoSubmitted.current || summary.auto_correlation_status === "completed") return undefined;
    autoSubmitted.current = true;
    const analysisId = summary.analysis_id;
    setAutoBusy(true);
    setAutoMsg(null);
    try {
      const r = await api.autoCorrelate(analysisId);
      if (currentAnalysis.current !== analysisId) return undefined;
      setSummary((current) => current ? { ...current, auto_correlation_status: "completed" } : current);
      setAutoMsg(
        r.total > 0
          ? `Correlation completed: ${r.total} variable(s): ${r.variables.join(", ")}. Open Generate to build the JMX.`
          : "No unambiguous response-to-request dependencies were found. Review placeholders and conflicting values under Add rule.",
      );
      await refresh();
      if (currentAnalysis.current === analysisId) setTab("graph");
      return r;
    } catch (e) {
      if (currentAnalysis.current !== analysisId) return undefined;
      autoSubmitted.current = false;
      setAutoMsg(e instanceof Error ? e.message : String(e));
      return undefined;
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

  function openAnalysis(s: AnalysisSummary, job: string | null = null) {
    currentAnalysis.current = s.analysis_id;
    autoSubmitted.current = false;
    setAutoBusy(false); setAutoMsg(null); setTab("health");
    setSummary(s); setRuleCount(s.rule_count); setJobId(job);
  }

  async function newAnalysis() {
    const ids = { analysisId: summary?.analysis_id ?? null, jobId };
    currentAnalysis.current = null; autoSubmitted.current = false;
    setAutoBusy(false); setAutoMsg(null); setSummary(null); setJobId(null);
    setWizardKey((k) => k + 1); // fresh wizard state
    await discardSession(ids);
  }

  return (
    <ToastProvider>
      <AppHeader right={<button className="btn-ghost whitespace-nowrap" onClick={newAnalysis}>New analysis</button>} />
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {!summary ? (
          <CollectionWizard key={wizardKey} onDone={(s, job) => openAnalysis(s, job)} onJobCreated={setJobId} />
        ) : (
          <Results
            summary={summary}
            tab={tab}
            setTab={setTab}
            ruleCount={ruleCount}
            refresh={refresh}
            autoBusy={autoBusy}
            autoCorrelateAll={autoCorrelateAll}
            autoMsg={autoMsg}
          />
        )}
      </main>
    </ToastProvider>
  );
}
