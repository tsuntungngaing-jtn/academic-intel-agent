#!/bin/bash
#SBATCH --partition=cpu6348
#SBATCH --job-name=academic_intel
#SBATCH --output=/gpfs/work/juntongfan24/logs/job_%j.log
#SBATCH --error=/gpfs/work/juntongfan24/logs/job_%j.log

set -euo pipefail

# Repository root on GPFS; override locally: export ACADEMIC_INTEL_HOME=/path/to/academic-intel-agent
REPO_ROOT="${ACADEMIC_INTEL_HOME:-/gpfs/work/juntongfan24/academic-intel-agent}"
cd "$REPO_ROOT"

# Legacy conda activate (adjust if your cluster uses: source ~/miniconda3/etc/profile.d/conda.sh && conda activate academic_agent)
source activate academic_agent

python main.py analyze
