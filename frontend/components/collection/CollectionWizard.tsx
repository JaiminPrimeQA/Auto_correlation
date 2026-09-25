"use client";

import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { api, type AnalysisSummary, type CollectionInspection } from "@/lib/api";
import { suppliedValuesFor, variablesToAsk } from "@/lib/executionJob";
import { Stepper } from "@/components/ui/Stepper";
import { fadeVariants, stepVariants } from "@/lib/motion";
import { ExecutionProgress } from "./ExecutionProgress";
import { FilesStep, type CollectionFiles } from "./FilesStep";
import { ReviewStep } from "./ReviewStep";
import { VariablesStep } from "./VariablesStep";

type Step = "files" | "variables" | "review" | "progress";
const ORDER: Step[] = ["files", "variables", "review", "progress"];
const STEP_LABELS = ["Files", "Variables", "Review", "Run"];

function newAttemptKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** "Run a Postman collection" mode (spec §7): Files → Variables → Review →
 * Run, then the existing analysis results. */
export function CollectionWizard({
  onDone,
}: {
  onDone: (summary: AnalysisSummary, jobId: string) => void;
}) {
  const [step, setStepState] = useState<Step>("files");
  const [direction, setDirection] = useState<1 | -1>(1);
  const reduce = useReducedMotion();
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

  function setStep(next: Step) {
    setDirection(ORDER.indexOf(next) >= ORDER.indexOf(step) ? 1 : -1);
    setStepState(next);
  }

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

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Stepper steps={STEP_LABELS} current={ORDER.indexOf(step)} />
      <AnimatePresence mode="wait" custom={direction} initial={false}>
        <motion.div
          key={step}
          custom={direction}
          variants={reduce ? fadeVariants : stepVariants}
          initial="enter"
          animate="center"
          exit="exit"
        >
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
            <ExecutionProgress
              jobId={jobId}
              onReady={(summary) => onDone(summary, jobId)}
              onRetry={retry}
              onStartOver={startOver}
            />
          )}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
