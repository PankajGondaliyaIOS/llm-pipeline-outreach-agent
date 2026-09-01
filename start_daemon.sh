#!/usr/bin/env bash
set -e

# Navigate to application root
cd /home/ubuntu/project1_email_agent

# Activate virtual environment
source .venv/bin/activate

# Execute daemon (omitting --dry-run for live operation)
exec python3 main.py