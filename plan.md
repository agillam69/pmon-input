# Plan: CFA State Incidents to PagerMon Bridge

## Discovery Summary

### Operating System
- **Platform:** Windows 11 (NT 10.0.26200.0)
- **Python:** 3.14.7
- **PM2:** Installed but no processes running
- **NSSM:** Not installed

### PagerMon Installation
- **Source location:** `C:\Users\agill\Downloads\CAD New Build Plan\pagermon-CFA Ingest\pagermon-0.3.13`
- **Version:** 0.3.13-beta
- **Status:** Not currently running. No `config.json` generated (only `default.json` exists), no `node_modules` installed. PM2 process list is empty. No node processes found. No service registered.
- **Database:** SQLite3 (default). Multiple `messages.db` files exist on disk from prior runs at other locations.

### PagerMon Ingestion API (confirmed from source inspection)

| Detail | Value |
|--------|-------|
| **Endpoint** | `POST /api/messages` |
| **Alt endpoint** | `POST /post/messages` (legacy) |
| **Auth method** | `apikey` HTTP header |
| **Content-Type** | `application/json` or `application/x-www-form-urlencoded` |
| **Required fields** | `address` (string), `message` (string) |
| **Optional fields** | `datetime` (unix timestamp integer), `source` (string, default `"UNK"`) |
| **Success response** | HTTP 200, body = inserted message ID (string) |
| **Duplicate response** | HTTP 200, body = `"Ignoring duplicate"` |
| **Filtered response** | HTTP 200, body = `"Ignoring filtered"` |
| **Auth failure** | HTTP 401, `{"error": "Authentication failed."}` |
| **Missing fields** | HTTP 500, `{"message": "Error - address or message missing"}` |
| **Body size limit** | 1 MB (Express body-parser config) |
| **Address format** | String, typically 7-digit zero-padded |
| **API keys config** | `auth.keys` array in `server/config/config.json` |

### Previous Bridge Attempt
- An incomplete `cfa-pagermon-bridge` exists at `C:\Users\agill\Downloads\CAD New Build Plan\pagermon-CFA Ingest\cfa-pagermon-bridge`
- Has correct project structure but was built for Linux (systemd service, `/etc/` paths, `/var/lib/` paths)
- Never deployed live on this Windows host per its own deployment report

### Key Decisions

| Item | Decision | Rationale |
|------|----------|-----------|
| **Build location** | `C:\Users\agill\OneDrive\Pagermon Data Injest App\cfa-pagermon-bridge` | User's designated workspace |
| **Target platform** | Windows (primary), Linux-compatible (systemd unit included) | PagerMon is on this Windows machine |
| **Windows service** | NSSM or PM2 (user choice) | systemd is Linux-only; PM2 is already installed |
| **Proposed capcode** | `9990001` | Per spec; will check for collision before use |
| **Source identifier** | `mazzanet-cfa` | Per spec |
| **State DB path** | `C:\Users\agill\OneDrive\Pagermon Data Injest App\cfa-pagermon-bridge\data\state.sqlite3` | Windows-appropriate, within project |

---

## Blocking Question

**PagerMon is not currently running.** Before live delivery can be tested, PagerMon needs to be started. The bridge can be fully built, tested with mocks, and run in dry-run mode without PagerMon running. Live delivery testing will require:

1. PagerMon to be installed (`npm install` in the server directory) and started
2. A real API key configured in PagerMon's `server/config/config.json`
3. That API key provided in the bridge's `.env` file

**The build will proceed through dry-run capability. Live delivery will wait for user confirmation.**

---

## Build Phases

### Phase 1: Project Structure and Configuration
- Create clean project layout in workspace
- `requirements.txt` with pinned dependencies: `requests`, `beautifulsoup4`, `python-dotenv`
- `requirements-dev.txt` adding `pytest`
- `.env.example` with placeholder values adapted for Windows paths
- `config.py` loading env vars with defaults and validation

**Files:**
```
cfa-pagermon-bridge/
  requirements.txt
  requirements-dev.txt
  .env.example
  .gitignore
  src/cfa_pagermon_bridge/__init__.py
  src/cfa_pagermon_bridge/config.py
```

### Phase 2: Fetcher Module
- Poll the CFA source URL with configurable interval
- Configurable User-Agent (`CFA-PagerMon-Bridge/1.0`)
- Connect timeout (5s) and read timeout (15s)
- Follow redirects, reject non-HTTPS destinations
- Bounded exponential backoff with jitter on failure (capped at 5 min)
- Response size cap (2 MiB configurable)
- No TLS verification bypass
- No overlapping poll runs

**File:** `src/cfa_pagermon_bridge/fetcher.py`

### Phase 3: Parser Module
- Extract text from all `<strong>` elements
- Normalize: decode entities, join text nodes, collapse whitespace, trim
- Validate against compiled regex:
  ```regex
  ^\(([A-Z0-9]{2,12})\)\s+\d{2}:\d{2}:\d{2}\s+\d{4}-\d{2}-\d{2}\s+.+\[\1\]$
  ```
- Require matching open `(ID)` and close `[ID]` identifiers
- Reject headings, weather text, oversized messages
- Return unique valid messages in source order
- Safe empty list on no matches or malformed HTML
- Log warning if no messages detected for extended period while fetches succeed

**File:** `src/cfa_pagermon_bridge/parser.py`

### Phase 4: Store Module (SQLite Deduplication)
- Separate SQLite database (not PagerMon's)
- Schema:
  - `message_hash` (SHA-256, unique key)
  - `message_text` (full normalized dispatch)
  - `identifier` (extracted brigade ID e.g. `DROM`)
  - `first_seen_utc` (UTC timestamp)
  - `dispatch_timestamp` (parsed from message if valid)
  - `delivery_state` (`pending` / `delivered` / `dead_letter`)
  - `attempt_count`
  - `next_attempt_utc`
  - `last_attempt_utc`
  - `delivered_utc`
  - `last_error_category`
  - `last_error_description` (sanitized/truncated)
- Transactional insert with hash uniqueness constraint
- Resume pending on restart
- Retain delivered hashes indefinitely (low volume)
- Safe schema init/migration
- Busy timeout configured
- Parameterized statements only

**File:** `src/cfa_pagermon_bridge/store.py`

### Phase 5: PagerMon Delivery Adapter
- Isolated behind interface (payload adjustable without changing other modules)
- POST to `{PAGERMON_BASE_URL}/api/messages`
- Headers: `apikey: {key}`, `Content-Type: application/json`, `X-Requested-With: XMLHttpRequest`
- JSON body:
  ```json
  {
    "address": "9990001",
    "message": "<full normalized dispatch text>",
    "datetime": <unix_timestamp>,
    "source": "mazzanet-cfa"
  }
  ```
- Interpret responses:
  - 200 + numeric body = success (delivered)
  - 200 + `"Ignoring duplicate"` = treat as delivered (PagerMon already has it)
  - 200 + `"Ignoring filtered"` = treat as delivered (PagerMon filtered it)
  - 401/403 = configuration failure, stop rapid retries, log clearly, mark unhealthy
  - 429/5xx = temporary, retry with bounded exponential backoff + jitter
  - Network error/timeout = temporary, retry
- Max retry count configurable (default 20), then `dead_letter`
- DRY_RUN mode: log what would be sent, do not POST, do not mark delivered

**File:** `src/cfa_pagermon_bridge/pagermon.py`

### Phase 6: Main Loop and CLI
- Async-safe polling loop (no overlap)
- Graceful SIGTERM/SIGINT handling (finish current DB transaction)
- `--check` mode: verify config, DB access, source fetch/parse, PagerMon reachability (no injection)
- `--dry-run` flag override
- `--test-delivery` with interactive confirmation and clearly labelled test text (`TEST - CFA WEB BRIDGE ...`)
- Structured logging to stdout/stderr (for journald on Linux, console on Windows)
- Log: event type, fetch result/duration, candidate/valid counts, shortened hash + identifier, delivery result, retry timing, queue counts
- Never log: API key, full headers, cookies, secrets
- Full dispatch text only at DEBUG level

**File:** `src/cfa_pagermon_bridge/main.py`

### Phase 7: Comprehensive Tests
All tests use mocked HTTP and temporary databases. No live network calls.

**Parser tests (`tests/test_parser.py`):**
1. DROM example extraction
2. UPPE style example
3. One, two, and three valid `<strong>` messages
4. Wrapped whitespace and nested spans inside `<strong>`
5. Bold headings and weather text ignored
6. Mismatched identifiers `(DROM)...[UPPE]` rejected
7. Missing opening or closing identifier rejected
8. Malformed timestamps/dates rejected
9. Duplicate elements in single page return one message
10. Empty and malformed HTML return empty list
11. Oversized content rejected
12. HTML entities decoded correctly

**Store tests (`tests/test_store.py`):**
1. First observation creates one pending item
2. Repeated observation does not duplicate
3. Successful mocked delivery marks delivered
4. Failed delivery remains pending, schedules retry
5. Restart simulation resumes pending delivery
6. Distinct text produces distinct hash/message
7. 401/403 causes configuration-failure behaviour
8. 429/5xx/network errors cause bounded retry
9. Dead-letter transition retains record
10. Dry-run never submits or falsely marks delivery

**Delivery tests (`tests/test_delivery.py`):**
- PagerMon adapter payload format verification
- Response interpretation (success, duplicate, filtered, auth failure, server error)
- Retry backoff calculation

**Fixture files (`tests/fixtures/`):**
- `drom.html` - page with DROM dispatch
- `uppe.html` - page with UPPE dispatch
- `multi.html` - page with multiple dispatches
- `empty.html` - page with no dispatches
- `weather.html` - page with weather/heading bold text only

### Phase 8: Service Deployment

**Windows (primary - PM2 is installed):**
- PM2 ecosystem config (`ecosystem.config.js`) to run via Python venv
- Instructions for `pm2 start`, `pm2 save`, `pm2 startup` (Windows auto-start)

**Linux (secondary - for future server deployment):**
- Hardened systemd unit file (`deploy/cfa-pagermon-bridge.service`)
- Dedicated unprivileged user
- `Restart=on-failure`
- Protected environment file loading
- Write access only to state directory
- `install.sh` script (idempotent, backup before replace, never delete state DB)

**Scripts:**
- `scripts/install.sh` (Linux)
- `scripts/install.ps1` (Windows)
- `scripts/status.sh` / `scripts/status.ps1`

### Phase 9: README and Documentation
- Purpose and data flow diagram
- Prerequisites
- PagerMon compatibility (v0.3.13-beta, POST /api/messages, apikey header)
- Configuration reference (all env vars)
- Installation (Windows with PM2 / Linux with systemd)
- Dry-run and check-mode instructions
- Service start/stop/status/log commands
- Database/state location
- Retry and duplicate behaviour explanation
- Manual rollback procedure
- Troubleshooting guide:
  - Source failure
  - Zero extracted messages
  - PagerMon authentication
  - Queue backlog
  - Database errors
- Uninstall procedure (preserves state DB unless explicit purge)

### Phase 10: Acceptance Test and Rollout
1. Run all automated tests
2. Run in dry-run mode against live Mazzanet page
3. Confirm extraction of current dispatch(es)
4. Confirm deduplication across polls
5. Verify exact outgoing payload shape (mocked)
6. Show user: endpoint, payload, capcode, source identifier
7. **Wait for user confirmation** before first live injection
8. If approved: enable PM2 service
9. Verify logs, state DB, delivery
10. Restart and verify no resend

---

## Payload Example (sanitized)

```json
{
  "address": "9990001",
  "message": "(DROM) 13:25:49 2026-08-08 ALERT DROM2 INCIC1 ASSIST AV WITH ENTRY 2 LEE ST ARTHURS SEAT /SEAHAZE ST //ARTHURS SEAT RD M 159 G12 (219526) F CDROM F260807324 [DROM]",
  "datetime": 1723108349,
  "source": "mazzanet-cfa"
}
```

Request:
```
POST /api/messages HTTP/1.1
Host: 127.0.0.1:3000
Content-Type: application/json
X-Requested-With: XMLHttpRequest
apikey: ********
```

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| PagerMon not running | Build and test with mocks; dry-run mode works without PagerMon |
| Mazzanet HTML format changes | Parser returns empty list safely; warning logged after configurable period |
| Mazzanet site down | Exponential backoff, cap at 5 min, resume normal after recovery |
| PagerMon auth failure | Stop rapid retries, log clearly, mark service unhealthy |
| Duplicate messages after restart | SHA-256 hash dedup in persistent SQLite; delivered hashes retained indefinitely |
| Address 9990001 already in use | Check before creating alias; ask user to choose alternative |
| State DB corruption | Fail safely and visibly; never silently recreate without backup |
| Clock/timezone drift | All operational timestamps in UTC; dispatch text preserved unchanged |

---

## Definition of Done

- [ ] Parser reliably extracts the supplied dispatch format
- [ ] All tests pass
- [ ] Deduplication survives process restarts
- [ ] Temporary source/PagerMon failures retried safely
- [ ] Secrets protected (never logged, never in source control)
- [ ] PagerMon accessed through confirmed API (`POST /api/messages` with `apikey` header)
- [ ] Bridge runs as auto-restarting service (PM2 on Windows / systemd on Linux)
- [ ] Documentation and rollback instructions complete
- [ ] Existing PagerMon installation and data remain intact
