import { api } from "./api";

/** Best effort: remove server-side data for the current session. The UI
 * resets regardless; anything left behind still expires after 30 minutes. */
export async function discardSession({ analysisId, jobId }: { analysisId: string | null; jobId: string | null }): Promise<void> {
  await Promise.allSettled([
    analysisId ? api.deleteAnalysis(analysisId) : Promise.resolve(),
    jobId ? api.deleteExecutionJob(jobId) : Promise.resolve(),
  ]);
}
