"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getApiBaseUrl } from "@/lib/api-base";
import { PaperCard, type PaperRecord } from "@/components/PaperCard";

const XJTLU_DOMAIN = "@xjtlu.edu.cn";
const POLL_MS = 5000;
const LS_EMAIL = "xjtlu_openalex_email_local";
const LS_DEEPSEEK = "xjtlu_deepseek_key_local_hint";
const LS_SEARCH = "xjtlu_research_search_hint";
const LS_ANALYZE_MODE = "xjtlu_analyze_mode";

export type AnalyzeMode = "recent" | "related";

function IconClock({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </svg>
  );
}

function IconRadar({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width={18}
      height={18}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M19 5l-1.5 1.5M22 12h-2" />
      <path d="M12 12 19 5" opacity={0.85} />
    </svg>
  );
}

type StartResponse = {
  ok?: boolean;
  job_id?: string | null;
  stdout?: string;
  stderr?: string | null;
};

type StartErrorDetail = {
  message?: string;
  error_detail?: string;
  stderr_formatted?: string | null;
  stdout_formatted?: string | null;
  stderr?: string | null;
  stdout?: string;
  returncode?: number;
};

type StatusResponse = {
  squeue_stdout?: string;
  squeue_stderr?: string | null;
  latest_log_tail?: string;
  latest_log_path?: string | null;
  progress_text?: string;
};

type LatestResultsResponse = {
  records?: PaperRecord[];
  count?: number;
  path?: string;
};

function jobLinePresent(squeueOut: string, jobId: string): boolean {
  const lines = squeueOut.split("\n").filter((l) => l.trim());
  if (lines.length <= 1) {
    return false;
  }
  return lines.some((line) => line.includes(jobId));
}

type ConsoleBlock = {
  text: string;
  tone?: "normal" | "fatal";
};

function appendConsoleBlock(
  prev: ConsoleBlock[],
  block: string,
  tone: ConsoleBlock["tone"] = "normal",
): ConsoleBlock[] {
  const trimmed = block.trimEnd();
  if (!trimmed) {
    return prev;
  }
  return [...prev, { text: trimmed, tone }];
}

export function AnalyzePanel() {
  const [phase, setPhase] = useState<"idle" | "polling" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [latest, setLatest] = useState<PaperRecord | null>(null);

  const [emailLocal, setEmailLocal] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [deepseekKey, setDeepseekKey] = useState("");
  const [analyzeMode, setAnalyzeMode] = useState<AnalyzeMode>("recent");
  const [hasReceivedProgress, setHasReceivedProgress] = useState(false);

  const [consoleBlocks, setConsoleBlocks] = useState<ConsoleBlock[]>([]);
  const terminalScrollRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    try {
      setEmailLocal(localStorage.getItem(LS_EMAIL) ?? "");
      setSearchQuery(localStorage.getItem(LS_SEARCH) ?? "");
      setDeepseekKey(localStorage.getItem(LS_DEEPSEEK) ?? "");
      const savedMode = localStorage.getItem(LS_ANALYZE_MODE);
      if (savedMode === "recent" || savedMode === "related") {
        setAnalyzeMode(savedMode);
      }
    } catch {
      /* private mode */
    }
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(LS_EMAIL, emailLocal);
    } catch {
      /* ignore */
    }
  }, [emailLocal]);

  useEffect(() => {
    try {
      localStorage.setItem(LS_SEARCH, searchQuery);
    } catch {
      /* ignore */
    }
  }, [searchQuery]);

  useEffect(() => {
    try {
      localStorage.setItem(LS_DEEPSEEK, deepseekKey);
    } catch {
      /* ignore */
    }
  }, [deepseekKey]);

  useEffect(() => {
    try {
      localStorage.setItem(LS_ANALYZE_MODE, analyzeMode);
    } catch {
      /* ignore */
    }
  }, [analyzeMode]);

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    const el = terminalScrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [consoleBlocks]);

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
      const block =
        data.progress_text?.trim() ||
        [
          "(no progress_text from API — please upgrade api_server)",
          data.squeue_stdout ?? "",
          data.latest_log_tail ?? "",
        ]
          .filter(Boolean)
          .join("\n");
      if (block.trim()) {
        setHasReceivedProgress(true);
      }

      const stillInQueue =
        id != null && jobLinePresent(data.squeue_stdout ?? "", id);

      setConsoleBlocks((prev) => {
        let next = appendConsoleBlock(prev, block);
        if (id != null && !stillInQueue) {
          next = appendConsoleBlock(
            next,
            "> [console] 作业已离开队列，正在拉取 latest_results …",
          );
        }
        return next;
      });

      if (id != null && !stillInQueue) {
        clearTimer();
        setPhase("idle");
        await fetchLatest();
      }
    },
    [clearTimer, fetchLatest],
  );

  useEffect(() => () => clearTimer(), [clearTimer]);

  const fullMailto =
    emailLocal.trim() === ""
      ? ""
      : `${emailLocal.trim()}${XJTLU_DOMAIN}`;

  const handleStartAnalyze = async () => {
    const interest = searchQuery.trim();
    if (!interest) {
      setPhase("error");
      setError(
        "请填写研究兴趣。留空时超算任务不会带上课题，也不会触发「先抓取再分析」，结果可能与当前关键词无关。",
      );
      setConsoleBlocks((prev) =>
        appendConsoleBlock(
          prev,
          "[致命错误] 研究兴趣为空：已阻止提交。",
          "fatal",
        ),
      );
      return;
    }
    const interestDisp = interest;
    const emailPayload =
      emailLocal.trim() === ""
        ? null
        : `${emailLocal.trim()}${XJTLU_DOMAIN}`;

    setError(null);
    clearTimer();
    setPhase("polling");
    setHasReceivedProgress(false);

    const modeLabel =
      analyzeMode === "recent" ? "近期前沿" : "高相关度深度探索";
    setConsoleBlocks([
      `[系统] 模式已锁定：${modeLabel}…`,
      `[系统] 指令已封装：课题=${interestDisp}，准备发送至西浦超算中心...`,
      "> academic_intel · session start（含 --crawl-first：按课题抓取 OpenAlex 后再分析）",
      emailPayload
        ? `> OPENALEX_MAILTO 提示：请在超算环境 export OPENALEX_MAILTO=${emailPayload}`
        : "> 提示：填写学校邮箱后，请在超算 .env 或 shell 中设置 OPENALEX_MAILTO 以进入礼貌池",
      `> 研究检索备忘（前端）: ${interest}`,
      "> 正在提交 sbatch …",
    ].filter(Boolean));

    try {
      const r = await fetch("/api/start_analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          interest: interest || null,
          email: emailPayload,
          mode: analyzeMode,
        }),
      });
      const data = (await r.json()) as StartResponse & { detail?: unknown };
      if (!r.ok) {
        if (data.detail && typeof data.detail === "object") {
          const d = data.detail as StartErrorDetail;
          throw new Error(
            d.error_detail ||
              d.stderr_formatted ||
              d.stdout_formatted ||
              d.message ||
              JSON.stringify(data.detail),
          );
        }
        const msg =
          typeof data.detail === "string"
            ? data.detail
            : JSON.stringify(data.detail ?? r.statusText);
        throw new Error(msg);
      }
      const jid = data.job_id?.trim() || null;
      setJobId(jid);
      setConsoleBlocks((prev) =>
        appendConsoleBlock(
          prev,
          `> sbatch: ${data.stdout ?? "(no stdout)"}${data.stderr ? `\n> stderr: ${data.stderr}` : ""}`,
        ),
      );

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
      const errorMsg = e instanceof Error ? e.message : String(e);
      setError(errorMsg);
      clearTimer();
      setConsoleBlocks((prev) =>
        appendConsoleBlock(prev, `[致命错误] 超算拒绝任务：${errorMsg}`, "fatal"),
      );
    }
  };

  return (
    <div className="mb-10 flex flex-col gap-6 lg:flex-row lg:items-start">
      {/* 西浦配置侧边栏 */}
      <aside className="w-full shrink-0 rounded-lg border border-amber-200/80 bg-amber-50/90 p-4 shadow-sm lg:sticky lg:top-4 lg:max-w-sm lg:self-start">
        <h2 className="font-serif text-base font-semibold text-amber-950">
          西浦 · 配置指南
        </h2>
        <div
          className="mt-3 rounded-md border border-amber-300/60 bg-white/80 p-3 text-sm leading-relaxed text-amber-950"
          role="note"
        >
          <p className="font-medium text-amber-900">
            [超算工作状态：办公区已锁门]
          </p>
          <p className="mt-2 text-amber-950/95">
            别担心，这是西浦超算的安全机制。为了让“搬砖工”能出门给你取
            DeepSeek 的数据，我们已为你配置好了“前台（学校代理）”。
          </p>
          <p className="mt-2 text-amber-950/95">
            如果你有自己的 DeepSeek Key，请填入下方。
          </p>
        </div>

        <label className="mt-4 block text-xs font-medium text-amber-950/90">
          DeepSeek API Key（仅浏览器备忘）
        </label>
        <input
          type="password"
          autoComplete="off"
          value={deepseekKey}
          onChange={(e) => setDeepseekKey(e.target.value)}
          placeholder="sk-…"
          className="mt-1 w-full rounded-md border border-amber-200 bg-white px-3 py-2 font-mono text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-stanford-red focus:outline-none focus:ring-1 focus:ring-stanford-red"
        />
        <p className="mt-1 text-xs text-amber-900/70">
          正式跑分析请在超算{" "}
          <code className="rounded bg-amber-100/80 px-1">.env</code> 中设置{" "}
          <code className="rounded bg-amber-100/80 px-1">DEEPSEEK_API_KEY</code>
          ；此处不会发送到后端。
        </p>

        <div className="mt-4 border-t border-amber-200/80 pt-4">
          <p className="text-xs font-medium text-slate-600">API 基址</p>
          <code className="mt-1 block break-all rounded bg-white/90 px-2 py-1 text-xs text-slate-800">
            {getApiBaseUrl()}
          </code>
          <p className="mt-1 text-xs text-slate-500">
            构建时设置{" "}
            <code className="text-[11px]">NEXT_PUBLIC_API_BASE_URL</code>
          </p>
        </div>
      </aside>

      {/* 主操作区 */}
      <section className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
        <h2 className="font-serif text-lg font-semibold text-slate-950">
          超算分析任务
        </h2>

        {/* 邮箱 — 在搜索框上方 */}
        <div className="mt-5">
          <label
            htmlFor="xjtlu-email-local"
            className="text-sm font-medium text-slate-800"
          >
            学校邮箱（OpenAlex 礼貌池）
          </label>
          <div className="mt-1 flex flex-wrap items-stretch gap-0 rounded-md border border-slate-200 bg-white shadow-sm focus-within:border-stanford-red focus-within:ring-1 focus-within:ring-stanford-red sm:flex-nowrap">
            <input
              id="xjtlu-email-local"
              type="text"
              inputMode="email"
              autoComplete="username"
              value={emailLocal}
              onChange={(e) => setEmailLocal(e.target.value.replace(/\s/g, ""))}
              placeholder="zhangsan18"
              className="min-w-0 flex-1 border-0 bg-transparent px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-0"
            />
            <span className="flex items-center border-t border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-500 sm:border-l sm:border-t-0">
              {XJTLU_DOMAIN}
            </span>
          </div>
          <p className="mt-1.5 text-xs text-slate-600">
            填写邮箱进入 OpenAlex 礼貌池，加速论文抓取
          </p>
          {fullMailto ? (
            <p className="mt-1 font-mono text-xs text-slate-500">
              完整地址：<span className="text-slate-700">{fullMailto}</span>
            </p>
          ) : null}
        </div>

        {/* 搜索 / 研究兴趣 */}
        <div className="mt-5">
          <label
            htmlFor="xjtlu-search"
            className="text-sm font-medium text-slate-800"
          >
            研究兴趣 / 检索
          </label>
          <input
            id="xjtlu-search"
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="关键词或一句话描述你的研究方向…"
            className="mt-1 w-full rounded-md border border-slate-200 px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-stanford-red focus:outline-none focus:ring-1 focus:ring-stanford-red"
          />
          <p className="mt-1.5 text-xs leading-relaxed text-slate-600">
            OpenAlex 全文检索以英文为主；中文课题会在超算上自动探针并用 DeepSeek
            扩展为英文检索式后再抓取。若需完全手工控制，可在超算{" "}
            <code className="rounded bg-slate-100 px-1">.env</code> 设置{" "}
            <code className="rounded bg-slate-100 px-1">OPENALEX_SEARCH</code>{" "}
            并改用命令行单独{" "}
            <code className="rounded bg-slate-100 px-1">crawl</code>。
          </p>
        </div>

        {/* 分析模式：搜索框下方、控制台（及按钮）上方 */}
        <div className="mt-5" role="radiogroup" aria-label="分析模式">
          <span className="text-sm font-medium text-slate-800">分析模式</span>
          <div className="mt-2 flex gap-2 rounded-lg border border-slate-200 bg-slate-50/80 p-1 sm:gap-1">
            <button
              type="button"
              role="radio"
              aria-checked={analyzeMode === "recent"}
              onClick={() => setAnalyzeMode("recent")}
              className={`flex min-h-[44px] flex-1 flex-col items-center justify-center gap-1 rounded-md px-3 py-2 text-center text-sm font-medium transition-colors sm:flex-row sm:gap-2 ${
                analyzeMode === "recent"
                  ? "bg-white text-stanford-red shadow-sm ring-1 ring-stanford-red/30"
                  : "text-slate-600 hover:bg-white/80"
              }`}
            >
              <IconClock
                className={
                  analyzeMode === "recent"
                    ? "text-stanford-red"
                    : "text-slate-500"
                }
              />
              <span>追踪前沿</span>
              <span className="hidden text-[11px] font-normal text-slate-500 sm:inline">
                （搜近期文章）
              </span>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={analyzeMode === "related"}
              onClick={() => setAnalyzeMode("related")}
              className={`flex min-h-[44px] flex-1 flex-col items-center justify-center gap-1 rounded-md px-3 py-2 text-center text-sm font-medium transition-colors sm:flex-row sm:gap-2 ${
                analyzeMode === "related"
                  ? "bg-white text-stanford-red shadow-sm ring-1 ring-stanford-red/30"
                  : "text-slate-600 hover:bg-white/80"
              }`}
            >
              <IconRadar
                className={
                  analyzeMode === "related"
                    ? "text-stanford-red"
                    : "text-slate-500"
                }
              />
              <span>深度探索</span>
              <span className="hidden text-[11px] font-normal text-slate-500 sm:inline">
                （快速找寻相关文章）
              </span>
            </button>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-slate-600">
            「深度探索」基于 AI 关联度，跨越时间寻找最相关的经典或核心文献。
          </p>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void handleStartAnalyze()}
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

        {/* 黑客感控制台 */}
        <div className="mt-4">
          <p className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-500">
            Slurm / 日志流
          </p>
          <div
            ref={terminalScrollRef}
            className="h-64 overflow-y-auto overflow-x-auto rounded-md border border-emerald-900/40 bg-[#0a0a0a] px-3 py-2 font-mono text-xs leading-relaxed text-emerald-400 shadow-inner"
            aria-live="polite"
            aria-label="任务状态控制台"
          >
            {phase === "polling" && !hasReceivedProgress ? (
              <div className="mb-3 text-amber-300">
                [通讯] 正在穿越学校防火墙，调配 GPU 计算资源...
                <span className="ml-1 inline-block animate-pulse text-emerald-300">
                  ▋
                </span>
              </div>
            ) : null}
            {consoleBlocks.length === 0 ? (
              <span className="text-emerald-600/70">
                $ 等待任务启动…（每 {POLL_MS / 1000}s 轮询 /status · progress_text）
              </span>
            ) : (
              consoleBlocks.map((block, i) => (
                <pre
                  key={i}
                  className={`mb-4 whitespace-pre-wrap break-words border-b pb-4 last:mb-0 last:border-b-0 last:pb-0 ${
                    block.tone === "fatal"
                      ? "border-red-800/50 text-red-400"
                      : "border-emerald-800/30 text-emerald-400"
                  }`}
                >
                  {block.text}
                </pre>
              ))
            )}
          </div>
        </div>

        {error ? (
          <p className="mt-3 text-sm text-red-700" role="alert">
            {error}
          </p>
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
    </div>
  );
}
