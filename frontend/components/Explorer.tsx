"use client";

import { useEffect, useState } from "react";
import { CheckIcon, ProhibitIcon } from "@phosphor-icons/react";
import { api, type ExecutionSummary } from "@/lib/api";
import { StatusPill } from "./Badge";

function KeyValues({ rows }: { rows: { name: string; value: string }[] }) {
  if (!rows?.length) return <p className="text-xs text-fg-subtle">none</p>;
  return (
    <table className="w-full text-xs">
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-b border-line">
            <td className="w-1/3 py-1 pr-2 align-top text-fg-muted">{r.name}</td>
            <td className="mono break-all py-1 text-fg">{r.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Detail({ analysisId, execId }: { analysisId: string; execId: string }) {
  const [data, setData] = useState<any>(null);
  const [tab, setTab] = useState<"req" | "resp">("resp");
  useEffect(() => {
    api.getExecution(analysisId, execId).then(setData);
  }, [analysisId, execId]);
  if (!data) return <div className="p-4 text-sm text-fg-subtle">Loading...</div>;
  const r = tab === "req" ? data.request : data.response;
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-line px-4 py-2">
        <div className="text-sm font-semibold">{data.name}</div>
        <div className="mono text-xs text-fg-muted">
          {data.request.method} {data.request.url}
        </div>
      </div>
      <div className="flex gap-1 border-b border-line px-3 py-2 text-xs">
        {(["resp", "req"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`rounded-[10px] px-2 py-1 ${tab === t ? "bg-accent text-accent-ink" : "text-fg-muted hover:bg-surface2"}`}
          >
            {t === "req" ? "Request" : "Response"}
          </button>
        ))}
      </div>
      <div className="flex-1 space-y-3 overflow-auto p-4">
        {tab === "resp" && data.response && (
          <div className="text-xs text-fg-muted">
            status {data.response.status} · {data.response.code} · {data.response.content_type}
          </div>
        )}
        {r && (
          <>
            {"query" in r && (
              <section>
                <h4 className="mb-1 text-xs font-semibold text-fg">Query</h4>
                <KeyValues rows={r.query} />
              </section>
            )}
            <section>
              <h4 className="mb-1 text-xs font-semibold text-fg">Headers</h4>
              <KeyValues rows={r.headers} />
            </section>
            {"cookies" in r && r.cookies?.length > 0 && (
              <section>
                <h4 className="mb-1 text-xs font-semibold text-fg">Cookies</h4>
                <KeyValues rows={r.cookies} />
              </section>
            )}
            <section>
              <h4 className="mb-1 text-xs font-semibold text-fg">Body</h4>
              <pre className="mono max-h-64 overflow-auto rounded-[10px] bg-surface2 p-3 text-xs text-fg">
                {formatBody(tab === "req" ? r.raw_body : r.raw_body)}
              </pre>
            </section>
          </>
        )}
        {data.assertions?.length > 0 && (
          <section>
            <h4 className="mb-1 text-xs font-semibold text-fg">Assertions</h4>
            <ul className="text-xs">
              {data.assertions.map((a: any, i: number) => (
                <li key={i} className={`flex items-center gap-1 ${a.failed ? "text-danger" : "text-ok"}`}>
                  {a.failed ? <ProhibitIcon size={14} aria-hidden /> : <CheckIcon size={14} aria-hidden />}
                  {a.name}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </div>
  );
}

function formatBody(body: string | null): string {
  if (!body) return "(empty)";
  try {
    return JSON.stringify(JSON.parse(body), null, 2);
  } catch {
    return body;
  }
}

export function Explorer({ analysisId }: { analysisId: string }) {
  const [execs, setExecs] = useState<ExecutionSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.listExecutions(analysisId).then((r) => {
      setExecs(r.items);
      if (r.items[0]) setSelected(r.items[0].id);
    });
  }, [analysisId]);

  const filtered = execs.filter(
    (e) => e.name.toLowerCase().includes(query.toLowerCase()) || e.url.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="grid gap-3 md:grid-cols-[320px_1fr]">
      <div className="card flex flex-col">
        <div className="border-b border-line p-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search requests..."
            className="w-full rounded-[10px] bg-surface2 px-2 py-1.5 text-sm outline-none placeholder:text-fg-subtle"
          />
        </div>
        <ul className="max-h-[70vh] overflow-auto">
          {filtered.map((e) => (
            <li key={e.id}>
              <button
                onClick={() => setSelected(e.id)}
                className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm ${
                  selected === e.id ? "bg-surface2" : "hover:bg-surface2/60"
                }`}
              >
                <span className="truncate">
                  <span className="mono mr-1 text-xs text-fg-subtle">{e.index + 1}</span>
                  {e.name}
                </span>
                <StatusPill code={e.status_code} />
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="card min-h-[400px]">
        {selected ? <Detail analysisId={analysisId} execId={selected} /> : null}
      </div>
    </div>
  );
}
