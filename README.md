# Academic Intel Agent (全学科学术情报助手)

🚀 **项目愿景**：为科研人员打造一个“全学科、零负担、自动化”的学术推手机器。

## 🌟 核心功能
- **全域监测**：基于 OpenAlex 监控全球 2.5 亿篇论文及新兴期刊。
- **精准推送**：根据用户设置的 Level 0-5 学科概念及关键词进行 AI 匹配。
- **隐私友好**：无需 CARSI 账号密码，仅推送原文链接，由用户在浏览器安全登录。
- **智能摘要**：利用大模型生成极简中文导读。

## 🛠️ 技术栈
- Python 3.10+
- OpenAlex API (Data)
- DeepSeek API (AI Processor)
- PostgreSQL (Storage)

## 运行方式（仓库根目录）

```bash
pip install -r requirements.txt
python main.py crawl          # OpenAlex → data/works_sample.jsonl（依赖 .env）
python main.py analyze        # DeepSeek 分析 → final_report.jsonl / Markdown
python main.py interactive    # 按编号浏览报告，可加 --cite 查引用
python main.py figures path/to/paper.pdf "10.1234/zenodo.x"   # 启发式插图 → data/figures/
```

将 PDF 放在 `data/pdfs/{与 DOI 对应的文件名}.pdf`（文件名规则与 `utils.pdf_visuals.sanitize_doi_for_filename` 一致），则在 `analyze` 的深度报告阶段会对 ≥95 分论文自动抽图，并写入 `ai.extracted_figures` 与 `deep_dive_tech_report.md` 附图节。

代码分层：`src/core`（配置与 LLM 传输）、`src/crawler`（OpenAlex）、`src/engine`（AI 流水线）、`src/storage`（JSONL/Markdown）、`src/ui`（交互）、`src/utils`（文本与控制台工具）。