"use client";

import { useState } from "react";
import { api, type AnalysisSummary, type CollectionInspection } from "@/lib/api";
import { suppliedValuesFor, variablesToAsk } from "@/lib/executionJob";
import { ExecutionProgress } from "./ExecutionProgress";
import { FilesStep, type CollectionFiles } from "./FilesStep";
import { ReviewStep } from "./ReviewStep";
import { VariablesStep } from "./VariablesStep";

type Step = "files" | "variables" | "review" | "progress";

const STEP_LABELS: { id: Step | "results"; label: string }[] = [
  { id: "files", label: "Files" },
  { id: "variables", label: "Variables" },
  { id: "review", label: "Scope and review" },
  { id: "progress", label: "Execution progress" },
  { id: "results", label: "Analysis results" },
];

function newAttemptKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** "Run a Postman collection" mode (spec §7): Files → Variables → Scope and
 * review → Execution progress → the existing analysis results. */
export function CollectionWizard({
  onDone,
  onBack,
}: {
  onDone: (summary: AnalysisSummary) => void;
  onBack: () => void;
}) {
  const [step, setStep] = useState<Step>("files");
  const [files, setFiles] = useState<CollectionFiles | null>(null);
  const [inspection, setInspection] = useState<CollectionInspection | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [folderId, setFolderId] = useState<string | null>(null);
  // One key per submission attempt: a repeated POST of the same attempt (a
  // double click, a lost response) returns the same job instead of a new one.
  const [attemptKey, setAttemptKey] = useState<string>(newAttemptKey);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const askedNames = inspection ? variablesToAsk(inspection).map((v) => v.name) : [];

  function clearSecrets() {
    if (!inspection) return;
    const secret = new Set(variablesToAsk(inspection).filter((v) => v.sensitive).map((v) => v.name));
    setValues((current) => Object.fromEntries(Object.entries(current).filter(([k]) => !secret.has(k))));
  }

  function startAttempt() {
    setAttemptKey(newAttemptKey());
    setSubmitError(null);
  }

  async function submit() {
    if (!files || !inspection) return;
    setSubmitError(null);
    try {
      const job = await api.createExecutionJob({
        collection: files.collection,
        environment: files.environment,
        folderId,
        suppliedValues: suppliedValuesFor(askedNames, values),
        idempotencyKey: attemptKey,
      });
      clearSecrets();
      setJobId(job.job_id);
      setStep("progress");
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : String(e));
    }
  }

  function retry() {
    setJobId(null);
    setStep("variables");
  }

  function startOver() {
    setJobId(null);
    setFiles(null);
    setInspection(null);
    setValues({});
    setFolderId(null);
    startAttempt();
    setStep("files");
  }

  const currentIndex = STEP_LABELS.findIndex((s) => s.id === step);

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Run a Postman collection</h1>
        <button className="btn-ghost text-xs" onClick={onBack}>
          Choose another mode
        </button>
      </div>
      <ol aria-label="Wizard steps" className="flex flex-wrap gap-2 text-xs">
        {STEP_LABELS.map((s, i) => (
          <li
            key={s.id}
            aria-current={i === currentIndex ? "step" : undefined}
            className={`rounded-full border px-3 py-1 ${
              i === currentIndex
                ? "border-brand bg-brand/10 text-white"
                : i < currentIndex
                  ? "border-edge text-slate-300"
                  : "border-edge/50 text-slate-500"
            }`}
          >
            <span className="text-slate-500">{i + 1}.</span> <span>{s.label}</span>
          </li>
        ))}
      </ol>

      {step === "files" && (
        <FilesStep
          initial={files}
          onInspected={(picked, result) => {
            setFiles(picked);
            setInspection(result);
            setValues({});
            setFolderId(null);
            startAttempt();
            setStep("variables");
          }}
        />
      )}
      {step === "variables" && inspection && (
        <VariablesStep
          inspection={inspection}
          values={values}
          onChange={setValues}
          onBack={() => setStep("files")}
          onContinue={() => {
            startAttempt();
            setStep("review");
          }}
        />
      )}
      {step === "review" && files && inspection && (
        <ReviewStep
          files={files}
          inspection={inspection}
          folderId={folderId}
          onFolderChange={(id) => {
            setFolderId(id);
            startAttempt();
          }}
          suppliedNames={askedNames}
          error={submitError}
          onBack={() => setStep("variables")}
          onSubmit={submit}
        />
      )}
      {step === "progress" && jobId && (
        <ExecutionProgress jobId={jobId} onReady={onDone} onRetry={retry} onStartOver={startOver} />
      )}
    </div>
  );
}
