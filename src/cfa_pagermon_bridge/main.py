"""Main entrypoint and service loop for the CFA State Incidents to PagerMon Bridge."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from typing import Optional

from . import __version__
from .config import Config, load_config, validate_config
from .fetcher import Fetcher
from .pagermon import PagerMonClient
from .parser import parse_messages
from .store import MessageStore

logger = logging.getLogger("cfa_pagermon_bridge")

_SHUTDOWN_REQUESTED = False


def _signal_handler(signum: int, frame: object) -> None:
    global _SHUTDOWN_REQUESTED
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.info("Received shutdown signal (%s); finishing current cycle...", sig_name)
    _SHUTDOWN_REQUESTED = True


def setup_logging(log_level: str) -> None:
    """Configure structured console logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def run_check(cfg: Config) -> int:
    """Run diagnostics: check configuration, database, live fetch/parse, and PagerMon reachability."""
    setup_logging(cfg.log_level)
    logger.info("=== Running CFA-PagerMon Bridge Diagnostics (Check Mode) ===")
    logger.info("Bridge Version: %s", __version__)
    logger.info("Source URL: %s", cfg.cfa_source_url)
    logger.info("PagerMon Base URL: %s", cfg.pagermon_base_url)
    logger.info("PagerMon Address: %s", cfg.pagermon_address)
    logger.info("PagerMon Source: %s", cfg.pagermon_source)
    logger.info("State DB Path: %s", cfg.state_db_path)
    logger.info("Dry Run Mode: %s", cfg.dry_run)

    # 1. Validate configuration
    errors = validate_config(cfg)
    if errors:
        for err in errors:
            logger.error("Configuration Error: %s", err)
        return 1
    logger.info("[OK] Configuration is valid")

    # 2. Check SQLite state database access
    try:
        store = MessageStore(cfg.state_db_path)
        store.open()
        counts = store.get_counts()
        store.close()
        logger.info("[OK] State database accessible. Message counts: %s", counts)
    except Exception as exc:
        logger.error("[FAIL] State database check failed: %s", exc)
        return 1

    # 3. Check source fetch and parse
    fetcher = Fetcher(cfg)
    logger.info("Testing connection to CFA source URL...")
    result = fetcher.fetch()
    fetcher.close()
    if not result.success:
        logger.error("[FAIL] CFA Source fetch failed: %s (Status: %s)", result.error, result.status_code)
        return 1

    logger.info("[OK] CFA Source fetch succeeded in %.1fms (Status: %s)", result.duration_ms, result.status_code)
    messages = parse_messages(result.html or "", max_message_length=cfg.max_message_length)
    logger.info("[OK] Parsed %d valid message(s) from source", len(messages))
    for i, msg in enumerate(messages, 1):
        logger.info("  %d. [%s] %s... -> (hash: %s...)", i, msg.identifier, msg.text[:60], msg.message_hash[:12])

    # 4. Check PagerMon reachability
    client = PagerMonClient(cfg)
    reachable, msg = client.check_reachable()
    client.close()
    if reachable:
        logger.info("[OK] PagerMon endpoint check: %s", msg)
    else:
        logger.warning("[WARN] PagerMon endpoint check: %s", msg)

    logger.info("=== Check Mode Completed Successfully ===")
    return 0


def run_test_delivery(cfg: Config, test_text: str) -> int:
    """Send a single verified test message to PagerMon."""
    setup_logging(cfg.log_level)
    logger.info("=== Test Message Delivery ===")

    if not test_text.startswith("TEST - CFA WEB BRIDGE"):
        logger.error("Test message rejected: Must start with 'TEST - CFA WEB BRIDGE'")
        return 1

    client = PagerMonClient(cfg)
    logger.info("Sending test message to %s (address: %s, source: %s)...", client.endpoint, cfg.pagermon_address, cfg.pagermon_source)
    res = client.deliver(test_text)
    client.close()

    if res.success:
        logger.info("[OK] Test delivery successful! Response ID / Status: %s", res.pagermon_id)
        return 0
    else:
        logger.error("[FAIL] Test delivery failed: [%s] %s", res.error_category, res.error_desc)
        return 1


def run_service(cfg: Config) -> int:
    """Main daemon polling loop."""
    setup_logging(cfg.log_level)
    logger.info("Starting CFA State Incidents to PagerMon Bridge v%s", __version__)
    logger.info("Source: %s (Poll Interval: %ds)", cfg.cfa_source_url, cfg.poll_interval_seconds)
    logger.info("PagerMon: %s (Address: %s, Source: %s)", cfg.pagermon_base_url, cfg.pagermon_address, cfg.pagermon_source)
    logger.info("State Database: %s", cfg.state_db_path)
    if cfg.dry_run:
        logger.warning("DRY RUN MODE ENABLED: Messages will be fetched and deduplicated, but NOT sent to PagerMon.")

    # Validate configuration on startup
    if not cfg.dry_run:
        errors = validate_config(cfg)
        if errors:
            for err in errors:
                logger.error("Configuration error: %s", err)
            logger.error("Bridge cannot start with invalid configuration. Set DRY_RUN=true or provide required parameters.")
            return 1

    store = MessageStore(cfg.state_db_path)
    store.open()
    fetcher = Fetcher(cfg)
    client = PagerMonClient(cfg)

    last_valid_seen = time.time()
    last_poll_time = 0.0

    try:
        while not _SHUTDOWN_REQUESTED:
            elapsed = time.monotonic() - last_poll_time
            if last_poll_time > 0 and elapsed < cfg.poll_interval_seconds:
                sleep_time = cfg.poll_interval_seconds - elapsed
                # Sleep in short increments for responsive signal handling
                while sleep_time > 0 and not _SHUTDOWN_REQUESTED:
                    chunk = min(0.5, sleep_time)
                    time.sleep(chunk)
                    sleep_time -= chunk
                if _SHUTDOWN_REQUESTED:
                    break

            last_poll_time = time.monotonic()

            # 1. Fetch source
            fetch_res = fetcher.fetch()
            if not fetch_res.success:
                logger.warning(
                    "Fetch failed (duration=%.1fms, consecutive_failures=%d): %s",
                    fetch_res.duration_ms,
                    fetcher._consecutive_failures,
                    fetch_res.error,
                )
                # Apply backoff delay if backoff is active
                backoff = fetcher.backoff_seconds
                if backoff > 0:
                    logger.info("Backing off for %.1fs before next attempt...", backoff)
                    time.sleep(min(backoff, 60.0))
                continue

            # 2. Parse HTML
            messages = parse_messages(fetch_res.html or "", max_message_length=cfg.max_message_length)
            now = time.time()

            if messages:
                last_valid_seen = now
            elif (now - last_valid_seen) >= cfg.no_message_warning_seconds:
                logger.warning(
                    "No valid CFA dispatches observed for %d seconds while page fetches succeed. Check if source layout has changed.",
                    int(now - last_valid_seen),
                )

            # 3. Store new observations
            new_count = 0
            for msg in messages:
                is_new = store.insert_if_new(
                    message_hash=msg.message_hash,
                    message_text=msg.text,
                    identifier=msg.identifier,
                    dispatch_time=msg.dispatch_time,
                    dispatch_date=msg.dispatch_date,
                )
                if is_new:
                    new_count += 1
                    logger.info("New dispatch observed: [%s] hash=%s", msg.identifier, msg.message_hash[:12])
                    logger.debug("Dispatch text: %s", msg.text)

            logger.info(
                "Poll completed: status=%s duration=%.1fms candidates=%d valid=%d new=%d",
                fetch_res.status_code,
                fetch_res.duration_ms,
                len(messages),
                len(messages),
                new_count,
            )

            # 4. Deliver pending queue
            pending_list = store.get_pending(limit=50)
            if pending_list:
                if cfg.dry_run:
                    logger.info("Dry-run active: %d pending message(s) in queue (delivery skipped)", len(pending_list))
                else:
                    logger.info("Processing delivery for %d pending message(s)...", len(pending_list))
                    for pending in pending_list:
                        if _SHUTDOWN_REQUESTED:
                            break

                        logger.info(
                            "Delivering [%s] (hash=%s, attempt=%d)...",
                            pending.identifier,
                            pending.message_hash[:12],
                            pending.attempt_count + 1,
                        )
                        deliv_res = client.deliver(
                            message_text=pending.message_text,
                            dispatch_date=pending.dispatch_date,
                            dispatch_time=pending.dispatch_time,
                        )

                        if deliv_res.success:
                            store.mark_delivered(pending.message_hash)
                            logger.info(
                                "Delivered [%s] (hash=%s) -> PagerMon ID: %s",
                                pending.identifier,
                                pending.message_hash[:12],
                                deliv_res.pagermon_id,
                            )
                        else:
                            store.mark_failed(
                                message_hash=pending.message_hash,
                                error_category=deliv_res.error_category or "unknown",
                                error_desc=deliv_res.error_desc or "",
                                max_attempts=cfg.max_delivery_attempts,
                            )
                            if deliv_res.is_config_error:
                                logger.error(
                                    "Critical delivery configuration error for [%s]: %s",
                                    pending.identifier,
                                    deliv_res.error_desc,
                                )
                            else:
                                logger.warning(
                                    "Temporary delivery failure for [%s] (%s): %s",
                                    pending.identifier,
                                    deliv_res.error_category,
                                    deliv_res.error_desc,
                                )

    finally:
        logger.info("Shutting down cleanly. Closing database and network connections...")
        client.close()
        fetcher.close()
        store.close()
        logger.info("Service stopped gracefully.")

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="CFA State Incidents to PagerMon Bridge",
        prog="cfa-pagermon-bridge",
    )
    parser.add_argument(
        "--env-file",
        dest="env_file",
        default=None,
        help="Path to environment file (e.g. .env or /etc/cfa-pagermon-bridge/bridge.env)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify configuration, state DB, source fetch/parse, and PagerMon reachability, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Override DRY_RUN=true to fetch and deduplicate without delivering to PagerMon",
    )
    parser.add_argument(
        "--test-delivery",
        dest="test_text",
        metavar="TEXT",
        help="Send a test message to PagerMon (must begin with 'TEST - CFA WEB BRIDGE')",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    args = parser.parse_args(argv)

    # Register signal handlers for clean shutdown
    try:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, AttributeError):
        pass  # In some thread or unusual environment

    # Load configuration
    cfg = load_config(args.env_file)
    if args.dry_run:
        from dataclasses import replace
        cfg = replace(cfg, dry_run=True)

    if args.check:
        return run_check(cfg)

    if args.test_text:
        return run_test_delivery(cfg, args.test_text)

    return run_service(cfg)


if __name__ == "__main__":
    sys.exit(main())
