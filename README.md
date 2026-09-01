# ZOGOEX Autonomous Email Outreach Engine

Production-ready, background-supervised B2B email engine. Audits mobile game monetization models using Google Gemini and delivers personalized liquidity proposals via Google Workspace SMTP.

---

## Architecture & System Design

**Architecture Pattern:** *Asynchronous Sequential Pipeline with a Persistent SQLite State Machine.*

* **State-Driven Workflow:** Enforces single-dispatch semantics with atomic transitions (`UNSENT` $\rightarrow$ `AUDITED` $\rightarrow$ `SENT`/`SKIPPED`).
* **Deterministic Guardrails:** Strict Pydantic V2 schema validation guarantees structured JSON parsing and eliminates downstream schema mismatch.
* **Non-Blocking Concurrency:** Independent worker loops (`asyncio.gather`) run decoupled LLM auditing and rate-throttled SMTP dispatch queues without thread-blocking I/O.

---

## 1. Production Supervision (`systemd`)

Closing an SSH terminal terminates standard user processes. This engine runs as a supervised **systemd** background daemon:

* **Automatic Restarts:** If upstream API rate limits trigger an unhandled failure or the host VM reboots, `systemd` handles restart policies.
* **Log Aggregation:** Standard output and error streams are captured directly into the OS journal (`journalctl`).
* **Least-Privilege Isolation:** Executes under an unprivileged user space rather than root.

---

## 2. Server Deployment & Setup

### Prerequisites & Repository Setup
SSH into your remote Ubuntu server and initialize the project:

```bash
# 1. Update package lists and install build tools & SQLite CLI
sudo apt update && sudo apt install -y python3-venv python3-pip git sqlite3

# 2. Clone repository
git clone <YOUR_GIT_REPO_URL> ~/project1_email_agent
cd ~/project1_email_agent

# 3. Create isolated virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 4. Install production dependencies
pip install --upgrade pip
pip install -r requirements.txt