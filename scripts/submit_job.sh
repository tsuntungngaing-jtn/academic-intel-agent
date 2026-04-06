#!/bin/bash
#SBATCH --partition=cpu6348
#SBATCH --job-name=academic_intel
#SBATCH --output=logs/job_%j.log
#SBATCH --error=logs/job_%j.log

set -euo pipefail

# Positional args from API: sbatch scripts/submit_job.sh "<interest>" "<email>" "<mode>"
USER_INTEREST="${1:-}"
USER_EMAIL="${2:-}"
USER_MODE="${3:-recent}"
export ACADEMIC_ANALYZE_MODE="$USER_MODE"

# Repository root from env override or inferred from script location.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${ACADEMIC_INTEL_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$REPO_ROOT"

# Ensure expected runtime folders exist regardless of install location.
mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/data"

# Keep logs directory tidy: remove files older than 7 days.
find "$REPO_ROOT/logs" -type f -mtime +7 -print -delete 2>/dev/null || true

# Legacy conda activate (adjust if your cluster uses: source ~/miniconda3/etc/profile.d/conda.sh && conda activate academic_agent)
source activate academic_agent

# Build argv: only pass --interest / --email when non-empty so default .env / prompts still work
py_cmd=(python main.py analyze)
if [ -n "$USER_INTEREST" ]; then
  py_cmd+=(--interest "$USER_INTEREST")
fi
if [ -n "$USER_EMAIL" ]; then
  py_cmd+=(--email "$USER_EMAIL")
fi
py_cmd+=(--mode "$USER_MODE")
"${py_cmd[@]}"
