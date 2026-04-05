import { PaperCard, type PaperRecord } from "@/components/PaperCard";

const sample: PaperRecord = {
  index_no: 1,
  title: "示例论文标题（可替换为 JSONL 数据）",
  publication_year: 2026,
  journal_name: "Example Journal",
  doi: "https://doi.org/10.0000/example",
  ai: {
    summary_zh:
      "这是中文摘要占位。接入 final_report.jsonl 后，将每条记录传入 PaperCard。",
    match_score: 72,
    relevance_level: "部分相关",
    extracted_figures: [],
  },
  ok: true,
};

export default function Home() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-10 sm:px-6">
      <h1 className="mb-2 font-serif text-2xl font-semibold text-slate-950">
        Stanford Academic UI
      </h1>
      <p className="mb-8 text-sm text-slate-600">
        米灰背景 <code className="text-xs">paper-bg</code>，卡片左侧 Stanford
        红装饰条；插图路径使用可选链，例如{" "}
        <code className="text-xs">data?.ai?.extracted_figures?.[0]</code>。
      </p>
      <PaperCard data={sample} />
    </main>
  );
}
