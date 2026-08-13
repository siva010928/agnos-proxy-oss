cd "$(dirname "$0")/.." && set -a && source .env 2>/dev/null && set +a && exec .venv-crewai/bin/python demo/crewai_demo.py
