# CFA State Incidents to PagerMon Bridge (`pmon-input`)

A reliable, lightweight Python bridge that polls the Country Fire Authority (CFA) Victoria State Incidents feed, extracts and validates dispatch messages, deduplicates records persistently via SQLite, and injects new messages into a local [PagerMon](https://github.com/pagermon/pagermon) instance via its REST API.

---

## Quick Start

Start from a fresh server:

```bash
git clone https://github.com/agillam69/pmon-input.git
cd pmon-input
chmod +x scripts/install-pm2.sh
./scripts/install-pm2.sh
```

Then open the web UI at `http://<server-ip>:8585` and set your **PagerMon API key**.

On Windows:

```powershell
git clone https://github.com/agillam69/pmon-input.git
Set-Location pmon-input
.\scripts\install.ps1
```

---

## Architecture & Data Flow

```
+-------------------------------------------------------------+
| CFA State Incidents Live Webpage                            |
| https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream   |
+-------------------------------------------------------------+
                              │
                              │ Polled (every 20s default)
                              ▼
+-------------------------------------------------------------+
| Fetcher Module (src/cfa_pagermon_bridge/fetcher.py)         |
| - Connect & Read timeouts (5s / 15s)                        |
| - Exponential backoff + jitter on failure                   |
| - 2 MB max response size limit                              |
+-------------------------------------------------------------+
                              │
                              │ HTML content
                              ▼
+-------------------------------------------------------------+
| Parser Module (src/cfa_pagermon_bridge/parser.py)           |
| - Extracts <strong> dispatch lines                          |
| - Normalizes whitespace & decodes HTML entities             |
| - Strict regex validation: (ID) HH:MM:SS YYYY-MM-DD ... [ID]|
| - Ignores headings, weather, non-matching identifiers       |
+-------------------------------------------------------------+
                              │
                              │ Validated Dispatches
                              ▼
+-------------------------------------------------------------+
| SQLite Store (src/cfa_pagermon_bridge/store.py)             |
| - Deduplication key: SHA-256(normalized_message)            |
| - State machine: pending -> delivered / dead_letter         |
| - Survives service restarts without duplicates              |
+-------------------------------------------------------------+
                              │
                              │ Pending messages (oldest first)
                              ▼
+-------------------------------------------------------------+
| PagerMon Delivery Adapter (src/cfa_pagermon_bridge/pagermon)|
| - POST /api/messages                                        |
| - Headers: apikey, Content-Type: application/json           |
| - Payload: address (9990001), message, datetime, source     |
+-------------------------------------------------------------+
                              │
                              │ HTTP 200 OK
                              ▼
+-------------------------------------------------------------+
| Local PagerMon Instance (http://127.0.0.1:3000)             |
+-------------------------------------------------------------+
```

---

## Features

- **Robust HTML Parsing:** Strict validation matching opening `(ID)` and closing `[ID]` brigade tags. Ignores weather updates, table headers, and malformed snippets.
- **Persistent Deduplication:** Uses SHA-256 hashes stored in SQLite with WAL mode to guarantee zero duplicate messages across service restarts.
- **Resilient Delivery:** Automatically retries temporary network timeouts and 5xx errors with exponential backoff and jitter. Non-retryable errors (e.g. 401 Unauthorized) fail fast and log diagnostics.
- **Dry-Run & Check Modes:** Complete built-in diagnostics (`--check`, `--dry-run`) to test fetching, parsing, and connectivity without injecting test data into PagerMon.
- **Dual-Platform Service Support:** Ready-to-use PM2 configuration for Windows and systemd unit for Linux.

---

## Configuration Reference

Configuration is loaded from environment variables or a `.env` file.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `CFA_SOURCE_URL` | `https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream` | Live CFA incident feed URL |
| `POLL_INTERVAL_SECONDS` | `20` | Interval between page fetches (seconds) |
| `HTTP_CONNECT_TIMEOUT_SECONDS`| `5` | Connection timeout for HTTP requests |
| `HTTP_READ_TIMEOUT_SECONDS` | `15` | Read timeout for HTTP requests |
| `MAX_RESPONSE_BYTES` | `2097152` (2 MB) | Maximum accepted HTTP response size |
| `USER_AGENT` | `CFA-PagerMon-Bridge/1.0` | User-Agent string sent to source |
| `PAGERMON_BASE_URL` | `http://127.0.0.1:3000` | Base URL of the local PagerMon server |
| `PAGERMON_API_KEY` | `replace_me` | PagerMon API key (from `server/config/config.json`) |
| `PAGERMON_ADDRESS` | `9990001` | Dedicated capcode / address for CFA dispatches |
| `PAGERMON_SOURCE` | `mazzanet-cfa` | Source identifier tag in PagerMon |
| `STATE_DB_PATH` | `data/state.sqlite3` | Location of bridge's SQLite database |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `MAX_DELIVERY_ATTEMPTS` | `20` | Max attempts before moving message to dead letter |
| `MAX_MESSAGE_LENGTH` | `2000` | Maximum character length for a valid dispatch |
| `NO_MESSAGE_WARNING_SECONDS` | `600` (10 min) | Warns if no messages seen during continuous 200 OK fetches |
| `DRY_RUN` | `false` | When `true`, fetches & deduplicates without sending to PagerMon |

---

## Installation & Setup

### 1. Quick install with PM2 (Windows)

```powershell
git clone https://github.com/agillam69/pmon-input.git
Set-Location pmon-input
.\scripts\install.ps1
```

The installer creates `.env` from `.env.example` in **dry-run** mode, starts the bridge under PM2, and saves the process list. The PM2 process is named **`bridge`**.

After install:
- Set your `PAGERMON_API_KEY` in `.env` or via the web UI at `http://<host>:8585`
- Restart the bridge: `pm2 restart bridge`

### 2. Quick install with PM2 (Linux)

```bash
git clone https://github.com/agillam69/pmon-input.git
cd pmon-input
chmod +x scripts/install-pm2.sh
./scripts/install-pm2.sh
```

The PM2 process is named **`bridge`**. Manage it with:

```bash
pm2 status
pm2 logs bridge
pm2 restart bridge
pm2 stop bridge
```

### 3. Linux (systemd) alternative

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
sudo systemctl enable --now cfa-pagermon-bridge
```

---

## Updating via Git Sync

To pull the latest version and restart:

```bash
cd pmon-input
git pull

# If requirements changed, update the venv
.venv/bin/pip install -r requirements.txt

# Restart the PM2 process
pm2 restart bridge
pm2 save
```

On Windows:

```powershell
Set-Location pmon-input
git pull
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
pm2 restart bridge
pm2 save
```

---

## CLI Options

| Command | Description |
| :--- | :--- |
| `python -m src.cfa_pagermon_bridge.main --check` | Runs diagnostic checks on config, database, source parsing, and PagerMon endpoint |
| `python -m src.cfa_pagermon_bridge.main --dry-run` | Runs polling loop in safe dry-run mode (no messages sent to PagerMon) |
| `python -m src.cfa_pagermon_bridge.main --env-file /path/to/.env` | Runs with custom environment configuration file |
| `python -m src.cfa_pagermon_bridge.main --test-delivery "TEST - CFA WEB BRIDGE - Test 1"` | Sends a single test message to PagerMon |
| `python -m src.cfa_pagermon_bridge.main --version` | Prints version number |

---

## Running Automated Tests

Run the complete pytest suite:
```powershell
pytest -v
```

Test coverage includes:
- **Parser (`test_parser.py`):** Real-world dispatches (`DROM`, `UPPE`, multi-line dispatches), HTML entity unescaping, wrapped nested span tags, layout headings rejection, and malformed/oversized content rejection.
- **Deduplication Store (`test_store.py`):** Transactional state transitions, uniqueness constraints, process restart recovery, and dead-letter retry limits.
- **PagerMon Delivery Adapter (`test_delivery.py`):** JSON payload construction, `apikey` authentication headers, 200 OK duplicate/filter handling, 401/403 configuration error handling, and 429/5xx retry backoff.
- **CLI & Configuration (`test_config.py`, `test_main.py`):** Type-safe config parsing, check mode execution, and test delivery guards.

---

## License

MIT License.
