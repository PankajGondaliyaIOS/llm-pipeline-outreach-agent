cat << 'EOF' > ~/project1_email_agent/start_daemon.sh
#!/usr/bin/env bash
set -e

# Navigate dynamically to the directory where this script lives
cd "$(dirname "$0")"

# Activate the local virtual environment
source .venv/bin/activate

# Execute the main daemon process
exec python3 main.py
EOF

chmod +x ~/project1_email_agent/start_daemon.sh
sudo systemctl restart outreach-agent.service
sudo systemctl status outreach-agent.service --no-pager