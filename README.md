# Cursor Build Instructions: CFA State Incidents to PagerMon Bridge

## Instruction to Cursor

Build and deploy the complete application described below. Work autonomously, but do not guess PagerMon's ingestion endpoint, authentication method, or request payload. Inspect the installed PagerMon source, configuration, and existing ingestion client first. Preserve the existing PagerMon installation and database. Do not modify PagerMon itself unless the user explicitly approves a necessary change.

Before making changes, report the detected operating system, PagerMon location/version, ingestion mechanism, proposed installation directory, service account, and any blocking uncertainty. Never display API keys, passwords, cookies, or other secrets in terminal output, logs, source control, or the final report.

## Objective

Create a lightweight server-side service that:

1. Polls the CFA State Incidents webpage at a configurable interval.
2. Extracts only valid bold CFA dispatch messages.
3. Detects all matching messages when the page contains between one and three dispatch lines/elements.
4. Sends each newly observed message to the locally installed PagerMon instance.
5. Does not resend messages merely because the source page refreshes.
6. Retries temporary failures without losing messages.
7. Starts automatically after server reboot and recovers from crashes.
8. Provides useful, privacy-conscious operational logs and health/status commands.

## Source

Default URL:

```text
https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream
```

The relevant HTML has been observed in this form:

```html
<strong style="font-size: 20">(DROM) 13:25:49 2026-08-08 ALERT DROM2 INCIC1 ASSIST AV WITH ENTRY 2 LEE ST ARTHURS SEAT /SEAHAZE ST //ARTHURS SEAT RD M 159 G12 (219526) F CDROM F260807324 [DROM]</strong>
```

The message content changes regularly. Do not search for `DROM`, `UPPE`, a specific incident number, address, date, or fixed message text.

## Required technology

Implement the bridge in Python 3 using a virtual environment. Prefer the standard library plus:

* `requests` for HTTP;
* `beautifulsoup4` for HTML parsing;
* `pytest` for automated tests.

Use the standard-library `sqlite3`, `hashlib`, `logging`, `re`, and configuration/environment facilities where practical. Pin direct runtime dependencies in `requirements.txt`. Do not introduce Selenium, Playwright, Chromium, Redis, Docker, or a large web framework unless inspection proves that normal HTTP retrieval cannot obtain the dispatch HTML.

## Project layout

Use a clean layout similar to:

```text
cfa-pagermon-bridge/
├── README.md
├── requirements.txt
├── .env.example
├── src/
│   └── cfa_pagermon_bridge/
│       ├── __init__.py
│       ├── config.py
│       ├── fetcher.py
│       ├── parser.py
│       ├── store.py
│       ├── pagermon.py
│       └── main.py
├── tests/
│   ├── fixtures/
│   ├── test_parser.py
│   ├── test_store.py
│   └── test_delivery.py
├── deploy/
│   └── cfa-pagermon-bridge.service
└── scripts/
    ├── install.sh
    └── status.sh
```

Adapt the layout if the server has an established convention, but keep application code, runtime state and secrets separate.

## Phase 1: inspect PagerMon safely

Perform read-only discovery before coding the delivery adapter:

1. Locate the PagerMon server and client installations, including process-manager or systemd configuration.
2. Determine the installed version or Git commit where possible.
3. Inspect its current ingestion client, routes and server handlers to identify:

   * endpoint path and HTTP method;
   * authentication header or parameter;
   * exact request fields and types;
   * how address/capcode, message body, source/identifier, protocol and timestamp are represented;
   * success status codes and response body;
   * duplicate-handling behaviour;
   * request timeouts or size limits.
4. Prefer the same supported API used by the installed PagerMon client. Do not write directly to PagerMon's SQLite/MySQL database.
5. Do not print secret values. It is acceptable to report that a key was found and where it is configured.
6. If no supported ingestion API can be confirmed, stop and give the user the evidence and options. Do not invent one.

The delivery adapter must be isolated behind a small interface so its payload can be adjusted without changing fetching, parsing or deduplication.

## Fetching requirements

* Poll interval must be configurable; default to 20 seconds.
* Use a descriptive configurable User-Agent, for example `CFA-PagerMon-Bridge/1.0`.
* Use explicit connect and read timeouts; a total request must not hang indefinitely.
* Follow ordinary redirects but reject unexpected non-HTTP(S) destinations.
* Treat non-2xx responses, timeouts, DNS failures and TLS failures as temporary fetch failures.
* Apply bounded exponential backoff with jitter after consecutive failures, capped at five minutes.
* Return to the normal interval after a successful fetch.
* Do not disable TLS certificate verification.
* Do not overlap polling runs.
* Cap the accepted response size at a reasonable configurable limit, such as 2 MiB.
* Parse the actual HTTP response; do not require a browser or saved-page resource directory.

## Extraction and validation requirements

Parse all `<strong>` elements, but validate their normalized text independently of CSS styling. The `style="font-size: 20"` attribute is a useful hint, not the sole definition of a message.

Normalize each candidate by:

* decoding HTML entities through the parser;
* joining wrapped/nested text nodes with spaces;
* collapsing repeated whitespace to one ordinary space;
* trimming leading and trailing whitespace;
* preserving all other message punctuation and capitalization.

A valid candidate must:

* begin with a parenthesized identifier such as `(DROM)`;
* end with a square-bracketed identifier such as `[DROM]`;
* have exactly the same identifier at both ends;
* use an identifier made from uppercase letters and digits, with a sensible bounded length;
* contain a plausible time and date near the beginning;
* remain below a configurable maximum message length;
* not be a heading such as `CFA State Incidents` or weather information.

Start with a validation expression conceptually equivalent to:

```regex
^\(([A-Z0-9]{2,12})\)\s+\d{2}:\d{2}:\d{2}\s+\d{4}-\d{2}-\d{2}\s+.+\[\1\]$
```

Implement this as maintainable compiled regex and validation code. Avoid making the parser unnecessarily brittle if harmless nested markup is introduced.

Return every unique valid message in source-page order. The parser must safely return an empty list if the page has no incidents or the layout changes. An empty extraction is not automatically an error; however, log a warning if valid messages have not been detected for a configurable extended period while fetches continue succeeding.

## Identity and PagerMon fields

For every extracted message retain:

* complete normalized dispatch text;
* opening identifier, e.g. `DROM`;
* source timestamp/date parsed from the message when valid;
* SHA-256 hash of the normalized complete message.

Use a dedicated PagerMon source/identifier such as:

```text
mazzanet-cfa
```

Use an agreed dedicated PagerMon address/capcode if PagerMon requires one. Default proposal:

```text
9990001
```

Suggested alias metadata to configure manually or through an existing supported PagerMon mechanism:

```text
Address: 9990001
Alias: CFA State Incidents
Agency: CFA
Source: mazzanet-cfa
```

Do not silently create or overwrite an alias if that address is already in use. Check first and ask the user to choose another address if there is a collision. Do not use the changing brigade identifier as a PagerMon capcode unless the user explicitly selects that mapping.

Submit the original normalized dispatch as the PagerMon message body. Do not rewrite, abbreviate or enrich the dispatch text.

## Durable deduplication and delivery

Use a separate SQLite database owned by this bridge. Do not use PagerMon's database. Store at least:

* message SHA-256 hash as a unique key;
* normalized message text;
* extracted identifier;
* first-seen time in UTC;
* source dispatch timestamp if parsed;
* delivery state: `pending`, `delivered`, or `dead_letter`;
* attempt count;
* next-attempt time;
* last attempt time;
* delivered time;
* last error category and a sanitized/truncated error description.

Required behaviour:

1. Insert a newly observed message transactionally as `pending` using its hash as a uniqueness constraint.
2. If the same normalized text is seen again, do not create or deliver a second copy.
3. Deliver pending messages oldest-first.
4. Mark a message `delivered` only after PagerMon returns its confirmed success response.
5. Retry network errors, timeouts, HTTP 429 and HTTP 5xx with bounded exponential backoff and jitter.
6. Treat authentication failures and persistent request-format errors as configuration failures: stop rapid retries, log clearly, and make the service unhealthy.
7. Use a configurable maximum retry count before moving an item to `dead_letter`; do not delete it.
8. On restart, resume pending work.
9. Keep delivered hashes long enough to prevent repeats after a restart. Default to indefinite retention because volume is small; provide an explicit maintenance command if pruning is later required.
10. Do not treat changes to an existing incident as duplicates if the complete normalized message text changes. Each distinct dispatch/update is a separate PagerMon message.

SQLite must use transactions, parameterized statements and safe schema initialization/migration. Set sensible busy timeouts. There is only one intended service instance; prevent accidental simultaneous instances with systemd and/or an application lock.

## Configuration and secrets

Configuration must come from a root-owned environment file, for example:

```text
/etc/cfa-pagermon-bridge/bridge.env
```

Support at least:

```dotenv
CFA_SOURCE_URL=https://mazzanet.net.au/cfa/?reg=state&magickey=cfastream
POLL_INTERVAL_SECONDS=20
HTTP_CONNECT_TIMEOUT_SECONDS=5
HTTP_READ_TIMEOUT_SECONDS=15
USER_AGENT=CFA-PagerMon-Bridge/1.0
PAGERMON_BASE_URL=http://127.0.0.1:3000
PAGERMON_API_KEY=replace_me
PAGERMON_ADDRESS=9990001
PAGERMON_SOURCE=mazzanet-cfa
STATE_DB_PATH=/var/lib/cfa-pagermon-bridge/state.sqlite3
LOG_LEVEL=INFO
MAX_DELIVERY_ATTEMPTS=20
DRY_RUN=false
```

Adjust names and PagerMon-specific fields after local discovery. Quote environment values safely in documentation. The real environment file must be mode `0600` and excluded from source control. `.env.example` must contain placeholders only.

Support `DRY_RUN=true`, which performs live fetching, parsing and deduplication diagnostics but does not submit messages or incorrectly mark them delivered. Clearly label dry-run output.

## Logging and observability

Log to stdout/stderr for journald. Use structured, concise log messages containing:

* event type;
* fetch result and duration;
* number of candidates and valid messages;
* shortened message hash and identifier;
* delivery attempt/result;
* retry timing;
* queue counts when useful.

Never log the PagerMon API key, complete request headers, cookies, database connection secrets, or full exception objects that may contain them. The full incident text may be logged only at `DEBUG`; normal logs should use identifier plus shortened hash.

Provide commands documented in the README for:

```bash
systemctl status cfa-pagermon-bridge
journalctl -u cfa-pagermon-bridge -f
```

Also provide an application `--check` mode that verifies configuration, database access, source fetch/parsing and PagerMon reachability without injecting a test incident. A separate explicit test-delivery function may exist, but it must require interactive confirmation or a deliberately supplied flag and must use clearly labelled test text.

## systemd deployment

Create a hardened systemd unit that:

* runs as a dedicated unprivileged system user;
* starts after networking and, where applicable, after the local PagerMon service;
* uses `Restart=on-failure` with a sensible delay;
* loads the protected environment file;
* uses the project's virtual-environment interpreter;
* sets an explicit working directory;
* grants write access only to its state directory;
* applies reasonable hardening compatible with outbound HTTPS and local PagerMon HTTP access;
* does not run as root;
* prevents multiple instances.

Use `/opt/cfa-pagermon-bridge` for immutable application files and `/var/lib/cfa-pagermon-bridge` for mutable state unless the server's established layout indicates a better location.

The installer must be idempotent where practical. It must not delete an existing state database or overwrite a real environment file. Before replacing deployed application files, make a timestamped backup or deploy atomically. Validate the unit with `systemd-analyze verify` when available.

## Testing requirements

Write automated tests before enabling live delivery. Tests must not contact the real source or live PagerMon.

Parser tests must cover:

1. The supplied `DROM` example.
2. The earlier `UPPE` style example.
3. One, two and three valid `<strong>` messages.
4. Wrapped whitespace and nested spans inside `<strong>`.
5. Other bold headings and weather text being ignored.
6. Mismatched `(DROM)...[UPPE]` identifiers being rejected.
7. Missing opening or closing identifiers being rejected.
8. Malformed timestamps/dates being rejected.
9. Duplicate elements in a single page returning only one message.
10. Empty and malformed HTML failing safely.
11. Oversized content being rejected.
12. HTML entities being decoded correctly.

Store/delivery tests must cover:

* first observation creates one pending item;
* repeated observation does not duplicate it;
* a successful mocked delivery marks it delivered;
* failed delivery remains pending and schedules retry;
* restart simulation resumes pending delivery;
* distinct text produces a distinct hash/message;
* 401/403 causes configuration-failure behaviour;
* 429/5xx/network errors cause bounded retry;
* dead-letter transition retains the record;
* dry-run never submits or falsely marks delivery.

Use temporary databases in tests. Mock time and HTTP responses where required. Run the complete suite and include the results in the deployment report.

## Acceptance test and rollout

Use this rollout sequence:

1. Run formatting/linting if configured and all automated tests.
2. Run the service manually in dry-run mode against the live page.
3. Confirm it extracts the currently displayed dispatch message(s).
4. Confirm repeated polls do not create duplicate pending records.
5. Use a mocked or isolated PagerMon endpoint to verify the exact outgoing payload.
6. Confirm the PagerMon endpoint and credentials using a non-injecting check if supported.
7. Show the user the discovered endpoint, sanitized payload shape, proposed capcode/address and source identifier.
8. Obtain user confirmation before the first live message injection if a live test record would be visible or trigger PagerMon plugins/notifications.
9. Enable and start the systemd service.
10. Verify service status, journald logs, state database and one genuine new-message delivery.
11. Restart the service and verify that the genuine message is not resent.

Do not use a real emergency-looking test message. Any approved live test must begin clearly with `TEST - CFA WEB BRIDGE` and should avoid notification-triggering keywords where possible.

## Failure handling

* A source outage must not crash-loop the service.
* An HTML layout change must produce a warning and retain existing queued messages.
* A PagerMon outage must queue messages for later delivery.
* A corrupt or inaccessible state database must fail safely and visibly; never discard and silently recreate it without backing it up and obtaining approval.
* System clock/timezone differences must not affect hash deduplication. Store operational timestamps in UTC while preserving the dispatch text unchanged.
* SIGTERM must stop cleanly without interrupting a database transaction.

## Documentation deliverables

Create a README containing:

* purpose and data flow;
* prerequisites;
* detected PagerMon compatibility details;
* configuration reference;
* installation and upgrade instructions;
* dry-run and check-mode instructions;
* service start/stop/status/log commands;
* database/state location;
* retry and duplicate behaviour;
* manual rollback procedure;
* troubleshooting for source failure, zero extracted messages, PagerMon authentication, queue backlog and database errors;
* uninstall procedure that preserves the state database unless an explicit purge is requested.

Also provide a short deployment report containing:

* files created or changed;
* PagerMon version and ingestion route detected;
* sanitized example payload;
* tests run and results;
* service status;
* current queue counts;
* whether live delivery is enabled;
* any manual actions still required.

## Definition of done

The task is complete only when:

* the parser reliably extracts the supplied dispatch format;
* all tests pass;
* deduplication survives process and server restarts;
* temporary source or PagerMon failures are retried safely;
* secrets are protected;
* PagerMon is accessed through its confirmed supported ingestion mechanism;
* the bridge runs as an unprivileged, automatically restarting systemd service;
* documentation and rollback instructions are complete;
* the existing PagerMon installation and data remain intact.

## Hard constraints

* Do not scrape by screen coordinates, OCR or screenshots.
* Do not depend on the fixed message text or a fixed brigade identifier.
* Do not write directly to PagerMon's database.
* Do not disable TLS verification.
* Do not expose secrets.
* Do not modify or delete existing PagerMon data.
* Do not send duplicate messages after restarts.
* Do not enable live injection until the installed PagerMon API and payload have been confirmed.
* Do not claim success without running and reporting the tests.

