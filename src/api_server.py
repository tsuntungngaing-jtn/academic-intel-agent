"""
FastAPI control plane for Academic Intel Agent on Slurm / GPFS.

Listens on port 9105 (XJTLU HPC policy: 9100–9200).

Run from the repo (``src`` on ``PYTHONPATH``)::

    cd /path/to/academic-intel-agent/src
    python api_server.py

Or::

    cd /path/to/academic-intel-agent
    set PYTHONPATH=src
    python -m uvicorn api_server:app --host 0.0.0.0 --port 9105
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

SBATCH_JOB_ID_RE = re.compile(r"Submitted batch job (\d+)", re.IGNORECASE)

# Runtime proxy overrides (merged into subprocess env); school proxy can be set via POST /config/proxy
_runtime_proxy: dict[str, Optional[str]] = {
    "http_proxy": None,
    "https_proxy": None,
}


def _project_root() -> Path:
    """Resolve project root from env override or this file location."""
    raw = os.getenv("ACADEMIC_INTEL_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _logs_dir() -> Path:
    raw = os.getenv("ACADEMIC_INTEL_SLURM_LOG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (_project_root() / "logs").resolve()


def _data_dir() -> Path:
    for key in ("ACADEMIC_INTEL_DATA_DIR", "DATA_DIR"):
        raw = os.getenv(key, "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    return (_project_root() / "data").resolve()


def _final_report_jsonl() -> Path:
    return (_data_dir() / "final_report.jsonl").resolve()


def _slurm_username() -> str:
    raw = os.getenv("ACADEMIC_INTEL_SLURM_USER", "").strip()
    if raw:
        return raw
    return os.getenv("USER", "").strip() or os.getenv("USERNAME", "").strip() or "unknown"


def _cors_origins() -> list[str]:
    raw = os.getenv("ACADEMIC_INTEL_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return ["*"]


app = FastAPI(title="Academic Intel API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _subprocess_env() -> dict[str, str]:
    env = {k: str(v) if v is not None else "" for k, v in os.environ.items()}
    for key in ("http_proxy", "https_proxy"):
        val = _runtime_proxy.get(key)
        if val:
            env[key] = val
            env[key.upper()] = val
    return env


class ProxyConfigBody(BaseModel):
    http_proxy: Optional[str] = Field(None, description="e.g. http://proxy.xjtlu.edu.cn:8080")
    https_proxy: Optional[str] = None


class AnalyzeRequest(BaseModel):
    """Fields passed through to ``submit_job.sh`` as positional args (interest, email, mode)."""

    email: Optional[str] = Field(
        default=None,
        max_length=2048,
        description="Full email for OPENALEX_MAILTO (e.g. user@xjtlu.edu.cn)",
    )
    interest: Optional[str] = Field(
        default=None,
        max_length=16000,
        description="Research interest string for ``python main.py analyze --interest``",
    )
    mode: str = Field(
        default="recent",
        max_length=32,
        description="``recent`` (追踪前沿) or ``related`` (深度探索)",
    )


def _sanitize_pass_through(value: Optional[str], *, max_len: int) -> str:
    """
    Normalize user input for argv passing (no shell involved in subprocess).

    Strips control characters and collapses whitespace so Slurm/bash/Python are
    not broken by newlines or NUL. For audit logs, use :func:`shlex.quote`.
    """
    if value is None:
        return ""
    s = value.strip()
    if not s:
        return ""
    s = s.replace("\x00", "")
    s = s.replace("\r", " ")
    s = " ".join(s.split())
    if len(s) > max_len:
        s = s[:max_len]
    return s


def _sanitize_mode(value: Optional[str]) -> str:
    """Allow only ``recent`` / ``related``; default ``recent``."""
    if value is None:
        return "recent"
    s = _sanitize_pass_through(value, max_len=32).lower()
    if s in ("recent", "related"):
        return s
    return "recent"


def _one_line_text(value: str) -> str:
    """Normalize multiline process output into a compact single line."""
    if not value:
        return ""
    return " | ".join([line.strip() for line in value.splitlines() if line.strip()])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/config/proxy")
def get_proxy_config() -> dict[str, Optional[str]]:
    """Effective proxy: runtime override wins, then process environment."""
    def pick(lower: str) -> Optional[str]:
        rt = _runtime_proxy.get(lower)
        if rt is not None and str(rt).strip() != "":
            return str(rt).strip()
        return (os.getenv(lower) or os.getenv(lower.upper()) or "").strip() or None

    return {
        "http_proxy": pick("http_proxy"),
        "https_proxy": pick("https_proxy"),
    }


@app.post("/config/proxy")
def set_proxy_config(body: ProxyConfigBody) -> dict[str, Optional[str]]:
    """Store proxy for subsequent subprocess calls (sbatch, squeue) and outbound tools."""
    if body.http_proxy is not None:
        v = body.http_proxy.strip()
        _runtime_proxy["http_proxy"] = v if v else None
    if body.https_proxy is not None:
        v = body.https_proxy.strip()
        _runtime_proxy["https_proxy"] = v if v else None
    return get_proxy_config()


@app.post("/start_analyze")
def start_analyze(
    body: Optional[AnalyzeRequest] = Body(default=None),
) -> dict[str, Any]:
    """Submit Slurm job via sbatch (analysis runs on compute node)."""
    req = body if body is not None else AnalyzeRequest()
    interest_s = _sanitize_pass_through(req.interest, max_len=16000)
    email_s = _sanitize_pass_through(req.email, max_len=2048)
    mode_s = _sanitize_mode(req.mode)

    logger.info(
        "[API] 模式锁定：%s，正在派单...",
        mode_s,
    )
    logger.info(
        "[API] 收到新订单：兴趣=%s, 邮箱=%s，正在派单至计算节点...",
        interest_s or "(未填)",
        email_s or "(未填)",
    )
    logger.debug(
        "sbatch argv shell 等价: sbatch scripts/submit_job.sh %s %s %s",
        shlex.quote(interest_s) if interest_s else "''",
        shlex.quote(email_s) if email_s else "''",
        shlex.quote(mode_s),
    )

    root = _project_root()
    logs_dir = _logs_dir()
    data_dir = _data_dir()
    logs_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    script = root / "scripts" / "submit_job.sh"
    if not script.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Missing Slurm script: {script}",
        )
    try:
        proc = subprocess.run(
            ["sbatch", str(script), interest_s, email_s, mode_s],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="sbatch timed out")
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="sbatch not found; run this API on a Slurm login/service node",
        )

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    job_id: Optional[str] = None
    m = SBATCH_JOB_ID_RE.search(out) or SBATCH_JOB_ID_RE.search(err)
    if m:
        job_id = m.group(1)

    if proc.returncode != 0:
        logger.error("sbatch failed rc=%s stdout=%s stderr=%s", proc.returncode, out, err)
        stderr_fmt = _one_line_text(err)
        stdout_fmt = _one_line_text(out)
        detail_text = stderr_fmt or stdout_fmt or f"sbatch exited with code {proc.returncode}"
        raise HTTPException(
            status_code=500,
            detail={
                "message": "sbatch failed",
                "error_detail": detail_text,
                "stderr_formatted": stderr_fmt or None,
                "stdout_formatted": stdout_fmt or None,
                "stdout": out,
                "stderr": err,
                "returncode": proc.returncode,
            },
        )

    return {
        "ok": True,
        "job_id": job_id,
        "stdout": out,
        "stderr": err or None,
        "project_root": str(root),
        "interest_passed": interest_s,
        "email_passed": email_s,
        "mode_passed": mode_s,
    }


def _run_squeue(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["squeue", *args],
            capture_output=True,
            text=True,
            timeout=60,
            env=_subprocess_env(),
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "squeue not found"
    except subprocess.TimeoutExpired:
        return 124, "", "squeue timed out"


def _tail_text_file(path: Path, max_lines: int = 48) -> str:
    dq: deque[str] = deque(maxlen=max(max_lines, 1))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                dq.append(line.rstrip("\n\r"))
    except OSError as e:
        return f"(could not read log: {e})"
    return "\n".join(dq)


def _latest_slurm_log_tail(max_lines: int = 48) -> tuple[Optional[str], str]:
    log_dir = _logs_dir()
    if not log_dir.is_dir():
        return None, f"(log directory missing: {log_dir})"
    candidates = sorted(
        log_dir.glob("job_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return None, "(no job_*.log files yet)"
    latest = candidates[0]
    return str(latest), _tail_text_file(latest, max_lines=max_lines)


@app.get("/status")
def status(
    job_id: Optional[str] = Query(None, description="If set, run squeue -j <id>"),
    log_lines: int = Query(48, ge=1, le=500),
) -> dict[str, Any]:
    """Slurm queue for the configured user and tail of the newest job log on GPFS."""
    user = _slurm_username()
    if job_id and job_id.strip():
        rc, out, err = _run_squeue(["-j", job_id.strip()])
        scope = f"job {job_id.strip()}"
    else:
        rc, out, err = _run_squeue(["-u", user])
        scope = f"user {user}"

    log_path, log_tail = _latest_slurm_log_tail(max_lines=log_lines)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    progress_parts = [
        f"[{ts}] · squeue · {scope} · exit={rc}",
        out or "(squeue stdout empty)",
    ]
    if err:
        progress_parts.append(f"stderr: {err}")
    progress_parts.append("")
    if log_path:
        progress_parts.append(f"--- Slurm log: {log_path} ---")
    progress_parts.append(log_tail or "(no log tail)")
    progress_text = "\n".join(progress_parts)

    return {
        "squeue_scope": scope,
        "squeue_returncode": rc,
        "squeue_stdout": out,
        "squeue_stderr": err or None,
        "latest_log_path": log_path,
        "latest_log_tail": log_tail,
        "slurm_logs_dir": str(_logs_dir()),
        "progress_text": progress_text,
    }


def _read_last_jsonl_records(path: Path, limit: int) -> list[dict[str, Any]]:
    if limit < 1 or not path.is_file():
        return []
    dq: deque[str] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s:
                    dq.append(s)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in dq:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


@app.get("/latest_results")
def latest_results(
    limit: int = Query(1, ge=1, le=500, description="Number of trailing JSONL records to return"),
) -> dict[str, Any]:
    """Last ``limit`` objects from ``data/final_report.jsonl`` (newest at end of list)."""
    path = _final_report_jsonl()
    records = _read_last_jsonl_records(path, limit)
    return {
        "path": str(path),
        "limit": limit,
        "count": len(records),
        "records": records,
    }


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.getenv("ACADEMIC_INTEL_API_HOST", "0.0.0.0")
    port = int(os.getenv("ACADEMIC_INTEL_API_PORT", "9105"))
    uvicorn.run(app, host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
