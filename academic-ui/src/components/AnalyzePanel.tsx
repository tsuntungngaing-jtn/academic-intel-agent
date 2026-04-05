"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getApiBaseUrl } from "@/lib/api-base";
import { PaperCard, type PaperRecord } from "@/components/PaperCard";

type StartResponse = {
  ok?: boolean;
  job_id?: string | null;
  stdout?: string;
  stderr?: string | null;
};

type StatusResponse = {
  squeue_stdout?: string;
  squeue_stderr?: string | null;
  latest_log_tail?: string;
  latest_log_path?: string | null;
};

type LatestResultsResponse = {
  records?: PaperRecord[];
  count?: number;
  path?: string;
};

const POLL_MS = 5000;

function jobLinePresent(squeueOut: string, jobId: string): boolean {
  const lines = squeueOut.split("\n").filter((l) => l.trim());
  if (lines.length <= 1) {
    return false;
  }
  return lines.some((line) => line.includes(jobId));
}

export function AnalyzePanel() {
  const [phase, setPhase] = useState<"idle" | "polling" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [statusDump, setStatusDump] = useState<string>("");
  const [latest, setLatest] = useState<PaperRecord | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const fetchLatest = useCallback(async () => {
    const base = getApiBaseUrl();
    const r = await fetch(`${base}/latest_results?limit=1`);
    if (!r.ok) {
      throw new Error(`latest_results HTTP ${r.status}`);
    }
    const data = (await r.json()) as LatestResultsResponse;
    const rec = data.records?.[0];
    if (rec) {
      setLatest(rec);
    }
  }, []);

  const fetchStatus = useCallback(
    async (id: string | null) => {
      const base = getApiBaseUrl();
      const q = id ? `?job_id=${encodeURIComponent(id)}` : "";
      const r = await fetch(`${base}/status${q}`);
      if (!r.ok) {
        throw new Error(`status HTTP ${r.status}`);
      }
      const data = (await r.json()) as StatusResponse;
      const parts: string[] = [];
      parts.push("--- squeue ---");
      parts.push(data.squeue_stdout ?? "");
      if (data.squeue_stderr) {
        parts.push("--- squeue stderr ---");
        parts.push(data.squeue_stderr);
      }
      parts.push("--- latest Slurm log tail ---");
      parts.push(data.latest_log_path ? `file: ${data.latest_log_path}` : "");
      parts.push(data.latest_log_tail ?? "");
      setStatusDump(parts.join("\n"));

      if (id && !jobLinePresent(data.squeue_stdout ?? "", id)) {
        clearTimer();
        setPhase("idle");
        await fetchLatest();
      }
    },
    [clearTimer, fetchLatest],
  );

  useEffect(() => () => clearTimer(), [clearTimer]);

  const onStart = async () => {
    setError(null);
    clearTimer();
    setPhase("polling");
    setStatusDump("正在提交 sbatch …");

    try {
      const base = getApiBaseUrl();
      const r = await fetch(`${base}/start_analyze`, { method: "POST" });
      const data = (await r.json()) as StartResponse & { detail?: unknown };
      if (!r.ok) {
        const msg =
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail ?? r.statusText);
        throw new Error(msg);
      }
      const jid = data.job_id?.trim() || null;
      setJobId(jid);
      await fetchStatus(jid);

      if (jid) {
        timerRef.current = setInterval(() => {
          void fetchStatus(jid).catch((e: unknown) => {
            setError(e instanceof Error ? e.message : String(e));
          });
        }, POLL_MS);
      } else {
        setPhase("idle");
        setError(
          "已提交 sbatch，但未解析到 Job ID；请用 squeue 查看队列，或检查 API 返回的 stdout。",
        );
      }
    } catch (e: unknown) {
      setPhase("error");
      setError(e instanceof Error ? e.message : String(e));
      clearTimer();
    }
  };

  return (
    <section className="mb-10 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
      <h2 className="font-serif text-lg font-semibold text-slate-950">
        超算分析任务
      </h2>
      <p className="mt-1 text-sm text-slate-600">
        API：{" "}
        <code className="rounded bg-slate-100 px-1 text-xs">
          {getApiBaseUrl()}
        </code>
        （通过 <code className="text-xs">NEXT_PUBLIC_API_BASE_URL</code>{" "}
        指向超算 IP:9105）
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void onStart()}
          disabled={phase === "polling"}
          className="rounded-md bg-stanford-red px-4 py-2 text-sm font-medium text-white shadow hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {phase === "polling" ? "已提交，轮询中…" : "开始分析"}
        </button>
        {jobId ? (
          <span className="text-sm text-slate-600">
            Job ID: <span className="font-mono">{jobId}</span>
          </span>
        ) : null}
      </div>

      {error ? (
        <p className="mt-3 text-sm text-red-700" role="alert">
          {error}
        </p>
      ) : null}

      {statusDump ? (
        <pre className="mt-4 max-h-64 overflow-auto rounded-md border border-slate-100 bg-slate-50 p-3 text-xs text-slate-800">
          {statusDump}
        </pre>
      ) : null}

      {latest ? (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-medium text-slate-700">
            最新结果（final_report.jsonl 末行）
          </h3>
          <PaperCard data={latest} />
        </div>
      ) : null}
    </section>
  );
}
