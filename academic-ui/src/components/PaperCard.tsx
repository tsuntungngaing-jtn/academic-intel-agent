import Image from "next/image";

export type PaperAi = {
  summary_zh?: string;
  match_score?: number;
  relevance_level?: string;
  extracted_figures?: string[];
};

export type PaperRecord = {
  index_no?: number;
  work_id?: string;
  title?: string | null;
  publication_year?: number | null;
  journal_name?: string | null;
  doi?: string | null;
  ai?: PaperAi | null;
  ok?: boolean;
};

function figureSrc(path: string): string {
  if (path.startsWith("/")) {
    return path;
  }
  return `/${path.replace(/^\.\//, "")}`;
}

export function PaperCard({ data }: { data: PaperRecord }) {
  const score = Math.min(
    100,
    Math.max(0, Number(data?.ai?.match_score ?? 0) || 0),
  );
  const firstFigure = data?.ai?.extracted_figures?.[0];
  const imgSrc = firstFigure ? figureSrc(firstFigure) : undefined;

  return (
    <article className="overflow-hidden rounded-lg border border-slate-200/80 border-l-4 border-l-stanford-red bg-white shadow-sm">
      <div className="min-w-0 p-4 sm:p-5">
        <h2 className="font-serif text-lg font-semibold leading-snug text-slate-950 sm:text-xl">
          {data?.title ?? "（无标题）"}
        </h2>

        <div className="mt-3 space-y-1 text-sm text-slate-600">
          {data?.journal_name != null && data.journal_name !== "" && (
            <p>{data.journal_name}</p>
          )}
          <p className="flex flex-wrap gap-x-3 gap-y-0.5">
            {data?.publication_year != null && (
              <span>{data.publication_year}</span>
            )}
            {data?.ai?.relevance_level != null &&
              data.ai.relevance_level !== "" && (
                <span>{data.ai.relevance_level}</span>
              )}
          </p>
        </div>

        <div className="mt-4">
          <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
            <span>匹配分</span>
            <span className="tabular-nums text-slate-700">{score}</span>
          </div>
          <div
            className="h-2 w-full overflow-hidden rounded-full bg-slate-200"
            role="progressbar"
            aria-valuenow={score}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full rounded-full bg-stanford-red transition-[width] duration-300"
              style={{ width: `${score}%` }}
            />
          </div>
        </div>

        {data?.ai?.summary_zh != null && data.ai.summary_zh.trim() !== "" && (
          <p className="mt-4 text-sm leading-relaxed text-slate-700">
            {data.ai.summary_zh}
          </p>
        )}

        {imgSrc != null && (
          <div className="relative mt-4 aspect-video w-full max-w-xl overflow-hidden rounded-md border border-slate-100 bg-slate-50">
            <Image
              src={imgSrc}
              alt={data?.title ? `Figure — ${data.title}` : "Paper figure"}
              fill
              className="object-contain"
              sizes="(max-width: 640px) 100vw, 36rem"
              unoptimized
            />
          </div>
        )}
      </div>
    </article>
  );
}
