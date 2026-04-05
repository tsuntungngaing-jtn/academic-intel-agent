"""Path helpers for FastAPI / Slurm deployment on GPFS (XJTLU HPC)."""

from __future__ import annotations

import os
from pathlib import Path

from core.config import project_root as _default_project_root


def hpc_project_root() -> Path:
    """
    Repository root on the cluster.

    Set ``ACADEMIC_INTEL_HOME`` to an absolute path under GPFS (e.g.
    ``/gpfs/work/juntongfan24/academic-intel-agent``). If unset, falls back to
    the directory that contains ``src`` (local dev).
    """
    raw = os.getenv("ACADEMIC_INTEL_HOME", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return _default_project_root()


def hpc_data_dir() -> Path:
    raw = os.getenv("ACADEMIC_INTEL_DATA_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    raw_data = os.getenv("DATA_DIR", "").strip()
    if raw_data:
        return Path(raw_data).expanduser().resolve()
    return (hpc_project_root() / "data").resolve()


def final_report_jsonl() -> Path:
    return (hpc_data_dir() / "final_report.jsonl").resolve()


def gpfs_work_root() -> Path:
    """Base work directory for the cluster user (logs, etc.)."""
    raw = os.getenv("ACADEMIC_INTEL_GPFS_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("/gpfs/work/juntongfan24").resolve()


def slurm_logs_dir() -> Path:
    raw = os.getenv("ACADEMIC_INTEL_SLURM_LOG_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (gpfs_work_root() / "logs").resolve()


def slurm_username() -> str:
    return os.getenv("ACADEMIC_INTEL_SLURM_USER", "juntongfan24").strip() or "juntongfan24"
