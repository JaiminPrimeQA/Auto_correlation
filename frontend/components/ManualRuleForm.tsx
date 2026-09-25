"use client";

import { useEffect, useState } from "react";
import { CheckIcon, WarningIcon } from "@phosphor-icons/react";
import { api, type ExecutionSummary, type Occurrence } from "@/lib/api";

function suggestExtractor(loc: string): { method: string; hint: string } {
  if (loc === "json_body") return { method: "json_path", hint: "$.path.to.value" };
  if (loc === "xml_body") return { method: "xpath2", hint: "//node" };
  if (loc === "header" || loc === "cookie") return { method: "regex", hint: "Name:\\s*(.+?)[\\r\\n]" };
  return { method: "boundary", hint: "left|||right" };
}

// The visible characters of a (possibly masked) value: a GUID shows as
// "ed****************f7", so the visible chars are "edf7" (start + end).
function visibleValue(masked: string): string {
  return (masked || "").replace(/[*]+/g, "").replace(/…|\.\.\./g, "").toLowerCase();
}

// Match the VALUE first, by its visible characters, so typing the start "ed",
// the end "f7", both together "edf7", or a full id "30626" all work. Path/key
// is matched only as a fallback (so "merchantGUID" or "[357]" still filter).
function occMatches(o: Occurrence, q: string): boolean {
  const query = q.trim().toLowerCase().replace(/\s+/g, "");
  if (!query) return true;
  if (visibleValue(o.value_masked).includes(query)) return true;
  const meta = `${o.location_type}:${o.canonical_path} ${o.key ?? ""}`.toLowerCase();
  return meta.includes(query);
}

export function ManualRuleForm({
  analysisId,
  onCreated,
}: {
  analysisId: string;
  onCreated: () => void;
}) {
  const [execs, setExecs] = useState<ExecutionSummary[]>([]);
  const [producerExec, setProducerExec] = useState("");
  const [sources, setSources] = useState<Occurrence[]>([]);
  const [producer, setProducer] = useState<Occurrence | null>(null);
  const [consumerExec, setConsumerExec] = useState("");
  const [sinks, setSinks] = useState<Occurrence[]>([]);
  const [consumer, setConsumer] = useState<Occurrence | null>(null);
  const [variable, setVariable] = useState("");
  const [method, setMethod] = useState("json_path");
  const [expr, setExpr] = useState("");
  const [errors, setErrors] = useState<string[]>([]);
  const [ok, setOk] = useState<string | null>(null);
  const [producerQuery, setProducerQuery] = useState("");
  const [consumerQuery, setConsumerQuery] = useState("");
  const [autoFind, setAutoFind] = useState<Awaited<ReturnType<typeof api.findConsumers>> | null>(null);
  const [autoBusy, setAutoBusy] = useState(false);

  useEffect(() => {
    api.listExecutions(analysisId).then((r) => setExecs(r.items));
  }, [analysisId]);

  useEffect(() => {
    if (producerExec) api.listOccurrences(analysisId, producerExec, "response").then((r) => setSources(r.items));
  }, [analysisId, producerExec]);
  useEffect(() => {
    if (consumerExec) api.listOccurrences(analysisId, consumerExec, "request").then((r) => setSinks(r.items));
  }, [analysisId, consumerExec]);

  async function pickProducer(o: Occurrence) {
    setProducer(o);
    const s = suggestExtractor(o.location_type);
    setMethod(s.method);
    setExpr(o.location_type === "json_body" ? o.canonical_path : s.hint);
    if (!variable) setVariable((o.key || o.canonical_path.split(/[.\[\]]/).filter(Boolean).pop() || "var").replace(/[^A-Za-z0-9_]/g, "_"));
    // Auto-find every later request that uses this value.
    setAutoFind(null);
    setAutoBusy(true);
    try {
      setAutoFind(await api.findConsumers(analysisId, o));
    } catch {
      setAutoFind(null);
    } finally {
      setAutoBusy(false);
    }
  }

  type FoundSink = { execution_id: string; location_type: string; canonical_path: string; key: string | null; wrapper: string | null };

  async function createWith(cons: FoundSink[], label: string) {
    if (!producer || !autoFind || cons.length === 0) return;
    setErrors([]);
    setOk(null);
    const varName = variable || autoFind.suggested_variable;
    try {
      await api.createRule(analysisId, {
        variable_name: varName,
        producer: {
          execution_id: producer.execution_id, side: "response",
          location_type: producer.location_type, canonical_path: producer.canonical_path,
          key: producer.key, raw_value: "",
        },
        consumers: cons.map((c) => ({
          execution_id: c.execution_id, side: "request", location_type: c.location_type,
          canonical_path: c.canonical_path, key: c.key, raw_value: "", wrapper: c.wrapper,
        })),
        extractor_method: autoFind.suggested_extractor,
        extractor_expression: autoFind.suggested_expression,
      });
      setOk(`${label} '${varName}' into ${cons.length} request(s). Add another below.`);
      onCreated();
      resetForm();
    } catch (e) {
      setErrors([e instanceof Error ? e.message : String(e)]);
    }
  }

  const autoCreate = () => autoFind && createWith(autoFind.consumers, "Auto-correlated");
  const autoCreateByName = () => autoFind && createWith(autoFind.name_matches, "Correlated by field name");

  function resetForm() {
    setProducer(null);
    setConsumer(null);
    setVariable("");
    setExpr("");
    setProducerQuery("");
    setConsumerQuery("");
    setAutoFind(null);
  }

  async function submit() {
    setErrors([]);
    setOk(null);
    if (!producer || !consumer) {
      setErrors(["Select a producer response value and a consumer request location."]);
      return;
    }
    try {
      await api.createRule(analysisId, {
        variable_name: variable,
        producer: {
          execution_id: producer.execution_id,
          side: "response",
          location_type: producer.location_type,
          canonical_path: producer.canonical_path,
          key: producer.key,
          raw_value: "",
        },
        consumers: [
          {
            execution_id: consumer.execution_id,
            side: "request",
            location_type: consumer.location_type,
            canonical_path: consumer.canonical_path,
            key: consumer.key,
            raw_value: "",
            wrapper: consumer.wrapper,
          },
        ],
        extractor_method: method,
        extractor_expression: expr,
      });
      setOk(`Rule ${variable} created. You can add another rule below (give it a new name).`);
      onCreated();
      resetForm();
    } catch (e) {
      setErrors([e instanceof Error ? e.message : String(e)]);
    }
  }

  return (
    <div className="card p-4">
      <h3 className="mb-3 text-sm font-semibold">Add correlation parameter</h3>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="text-xs text-fg-muted">Producer request (response source)</label>
          <select
            className="mt-1 w-full rounded-[10px] bg-surface2 px-2 py-1.5 text-sm"
            value={producerExec}
            onChange={(e) => setProducerExec(e.target.value)}
          >
            <option value="">select...</option>
            {execs.map((e) => (
              <option key={e.id} value={e.id}>
                {e.index + 1}. {e.name}
              </option>
            ))}
          </select>
          {sources.length > 0 && (
            <input
              value={producerQuery}
              onChange={(e) => setProducerQuery(e.target.value)}
              placeholder="search by value: start/end/both, e.g. ed · f7 · edf7 · 30626"
              className="mt-2 w-full rounded-[10px] bg-surface2 px-2 py-1 text-xs"
            />
          )}
          <div className="mt-1 max-h-40 overflow-auto rounded-[10px] border border-line">
            {sources.filter((o) => occMatches(o, producerQuery)).map((o, i) => (
              <button
                key={i}
                onClick={() => pickProducer(o)}
                className={`block w-full px-2 py-1 text-left text-xs ${
                  producer?.canonical_path === o.canonical_path && producer?.location_type === o.location_type
                    ? "bg-accent-soft"
                    : "hover:bg-surface2"
                }`}
              >
                <span className="mono">{o.location_type}:{o.canonical_path}</span>{" "}
                <span className="text-fg-subtle">= {o.value_masked}</span>
              </button>
            ))}
            {sources.length > 0 && sources.filter((o) => occMatches(o, producerQuery)).length === 0 && (
              <p className="px-2 py-1 text-xs text-fg-subtle">No values match "{producerQuery}".</p>
            )}
          </div>
        </div>
        <div>
          <label className="text-xs text-fg-muted">Consumer request (sink)</label>
          <select
            className="mt-1 w-full rounded-[10px] bg-surface2 px-2 py-1.5 text-sm"
            value={consumerExec}
            onChange={(e) => setConsumerExec(e.target.value)}
          >
            <option value="">select...</option>
            {execs.map((e) => (
              <option key={e.id} value={e.id}>
                {e.index + 1}. {e.name}
              </option>
            ))}
          </select>
          {sinks.length > 0 && (
            <input
              value={consumerQuery}
              onChange={(e) => setConsumerQuery(e.target.value)}
              placeholder="search by value: start/end/both, e.g. 6d · 77 · webhookID"
              className="mt-2 w-full rounded-[10px] bg-surface2 px-2 py-1 text-xs"
            />
          )}
          <div className="mt-1 max-h-40 overflow-auto rounded-[10px] border border-line">
            {sinks.filter((o) => occMatches(o, consumerQuery)).map((o, i) => (
              <button
                key={i}
                onClick={() => setConsumer(o)}
                className={`block w-full px-2 py-1 text-left text-xs ${
                  consumer?.canonical_path === o.canonical_path && consumer?.location_type === o.location_type
                    ? "bg-accent-soft"
                    : "hover:bg-surface2"
                }`}
              >
                <span className="mono">{o.location_type}:{o.canonical_path}</span>{" "}
                <span className="text-fg-subtle">= {o.value_masked}</span>
              </button>
            ))}
            {sinks.length > 0 && sinks.filter((o) => occMatches(o, consumerQuery)).length === 0 && (
              <p className="px-2 py-1 text-xs text-fg-subtle">No values match "{consumerQuery}".</p>
            )}
          </div>
        </div>
      </div>

      {/* Auto-correlate panel: appears when a producer value is selected */}
      {producer && (
        <div className="mt-3 rounded-[10px] border border-accent/40 bg-accent-soft p-3 text-xs">
          {autoBusy && <p className="text-fg-muted">Searching later requests for this value...</p>}
          {!autoBusy && autoFind && autoFind.consumers.length > 0 && (
            <>
              <p className="flex items-center gap-1 text-ok">
                <CheckIcon size={14} aria-hidden />
                This value is reused in <b>{autoFind.consumers.length}</b> later request(s), it can be
                auto-correlated:
              </p>
              <ul className="mt-1 space-y-0.5">
                {autoFind.consumers.map((c, i) => (
                  <li key={i} className="text-fg">
                    → #{c.request_index + 1} {c.request_name.split("/").pop()} ·{" "}
                    <span className="mono">{c.location_type}:{c.key || c.canonical_path}</span>
                    {c.wrapper && <span className="text-fg-subtle"> (wraps as "{c.wrapper}...")</span>}
                  </li>
                ))}
              </ul>
              <p className="mt-1 text-fg-subtle">
                extractor <span className="mono">{autoFind.suggested_extractor}</span> ·{" "}
                <span className="mono">{autoFind.suggested_expression}</span>
              </p>
              <button className="btn mt-2" onClick={autoCreate}>
                Auto-correlate into {autoFind.consumers.length} request(s)
              </button>
            </>
          )}
          {!autoBusy && autoFind && autoFind.name_matches.length > 0 && (
            <div className={autoFind.consumers.length > 0 ? "mt-3 border-t border-line pt-2" : ""}>
              <p className="flex items-center gap-1 text-warn">
                <WarningIcon size={14} aria-hidden />
                Also found <b>{autoFind.name_matches.length}</b> later request(s) with a matching{" "}
                <b>field name</b> but a different captured value, likely correlations too (this replaces a
                hardcoded value with the real one). Review before trusting:
              </p>
              <ul className="mt-1 space-y-0.5">
                {autoFind.name_matches.map((c, i) => (
                  <li key={i} className="text-fg">
                    → #{c.request_index + 1} {c.request_name.split("/").pop()} ·{" "}
                    <span className="mono">{c.location_type}:{c.key || c.canonical_path}</span>{" "}
                    <span className="text-fg-subtle">
                      ("{c.producer_field}" ≈ "{c.consumer_field}")
                    </span>
                  </li>
                ))}
              </ul>
              <button className="btn-ghost mt-2 text-warn" onClick={autoCreateByName}>
                Correlate by field name (review) → {autoFind.name_matches.length} request(s)
              </button>
            </div>
          )}
          {!autoBusy && autoFind && autoFind.consumers.length === 0 && autoFind.name_matches.length === 0 && (
            <p className="text-warn">
              No later request uses this value (by exact match or field name), so it isn't a correlation.
              It's likely an input (parameter) or a credential: handle it via a User Defined Variable /
              property instead.
            </p>
          )}
          {!autoBusy && autoFind?.is_noise_source && (
            <p className="mt-1 text-fg-subtle">Note: this response header is usually infrastructure noise.</p>
          )}
        </div>
      )}

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <div>
          <label className="text-xs text-fg-muted">Variable name</label>
          <input
            className="mono mt-1 w-full rounded-[10px] bg-surface2 px-2 py-1.5 text-sm"
            value={variable}
            onChange={(e) => setVariable(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs text-fg-muted">Extractor</label>
          <select
            className="mt-1 w-full rounded-[10px] bg-surface2 px-2 py-1.5 text-sm"
            value={method}
            onChange={(e) => setMethod(e.target.value)}
          >
            {["json_path", "json_jmespath", "xpath2", "regex", "boundary"].map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-fg-muted">Expression</label>
          <input
            className="mono mt-1 w-full rounded-[10px] bg-surface2 px-2 py-1.5 text-sm"
            value={expr}
            onChange={(e) => setExpr(e.target.value)}
          />
        </div>
      </div>

      {errors.map((e, i) => (
        <p key={i} className="mt-2 rounded-[10px] bg-danger-soft px-3 py-2 text-xs text-danger">
          {e}
        </p>
      ))}
      {ok && <p className="mt-2 rounded-[10px] bg-ok-soft px-3 py-2 text-xs text-ok">{ok}</p>}

      <button className="btn mt-3" onClick={submit}>
        Create rule
      </button>
    </div>
  );
}
