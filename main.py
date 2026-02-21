"""
Token Snapshot Collector - Main Entry Point

Listens to Telegram source channels, parses buy signals,
collects API snapshots, and stores in SQLite for later analysis.
"""
import asyncio
import logging
import time
from telethon import TelegramClient, events

from config import API_ID, API_HASH, BOT_TOKEN, SOURCE_CHANNEL, SOURCE_CHANNEL_DEGEN_ONLY, validate_config
from parser import parse_signal, extract_symbol_from_message
from collector import collect_snapshot, refresh_sol_price, sol_price_refresh_loop
from database import init_database, token_exists, insert_snapshot

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("snapshot_collector.log")
    ]
)
logger = logging.getLogger(__name__)

# Stats
stats = {
    "messages_received": 0,
    "signals_parsed": 0,
    "snapshots_collected": 0,
    "duplicates_skipped": 0,
    "errors": 0,
    "start_time": time.time()
}


async def process_message(text: str, source_channel_id: int):
    """Process a single message from source channel"""
    stats["messages_received"] += 1

    # Determine source channel name
    source_name = "DEGEN" if source_channel_id == SOURCE_CHANNEL_DEGEN_ONLY else "MAIN"

    # Parse the signal
    parsed = parse_signal(text, str(source_channel_id))

    if not parsed:
        return  # Not a valid buy signal

    stats["signals_parsed"] += 1

    contract_address = parsed["contract_address"]
    wallet_name = parsed["wallet_name"]

    # Check if already in database
    if token_exists(contract_address):
        stats["duplicates_skipped"] += 1
        logger.debug(f"⏭️ [{source_name}] {contract_address[:8]} already in DB")
        return

    # Collect snapshot via API
    try:
        logger.info(f"📡 [{source_name}] Collecting {wallet_name} signal: {contract_address[:8]}...")

        snapshot = await collect_snapshot(contract_address)

        # Merge parsed signal data into snapshot
        snapshot["wallet_name"] = parsed["wallet_name"]
        snapshot["wallet_address"] = parsed["wallet_address"]
        snapshot["source_channel"] = parsed["source_channel"]
        snapshot["signal_mc_usd"] = parsed["signal_mc_usd"]
        snapshot["signal_lq_usd"] = parsed["signal_lq_usd"]
        snapshot["seen_minutes"] = parsed["seen_minutes"]

        # Extract symbol from message if not from API
        if not snapshot.get("symbol"):
            snapshot["symbol"] = extract_symbol_from_message(text)

        # Insert into database (sol_price_at_signal already set by collect_snapshot)
        if insert_snapshot(snapshot):
            stats["snapshots_collected"] += 1

            # Log snapshot summary
            symbol = snapshot.get("symbol", "???")
            mc = snapshot.get("api_mc_usd", 0) or 0
            curve = snapshot.get("curve_percentage", "?")
            holders = snapshot.get("holders", "?")
            risk = snapshot.get("risk_score", "?")

            logger.info(
                f"📸 {wallet_name} | ${symbol} | "
                f"MC:${mc:.0f} | curve:{curve} | holders:{holders} | risk:{risk}"
            )
        else:
            logger.warning(f"Failed to insert snapshot for {contract_address[:8]}")

    except Exception as e:
        stats["errors"] += 1
        logger.error(f"Error collecting snapshot for {contract_address[:8]}: {e}")


def print_stats():
    """Print current statistics"""
    uptime = time.time() - stats["start_time"]
    hours = uptime / 3600

    logger.info(
        f"📊 Stats: {stats['snapshots_collected']} snapshots | "
        f"{stats['signals_parsed']} signals | "
        f"{stats['duplicates_skipped']} dupes | "
        f"{stats['errors']} errors | "
        f"uptime: {hours:.1f}h"
    )


async def periodic_stats():
    """Print stats every hour"""
    while True:
        await asyncio.sleep(3600)
        print_stats()


async def main():
    """Main entry point"""
    logger.info("🚀 Token Snapshot Collector starting...")

    # Validate configuration
    validate_config()
    logger.info("✅ Configuration validated")

    # Initialize database
    init_database()
    logger.info("✅ Database initialized")

    # Initialize SOL price (first fetch)
    logger.info("💰 Fetching initial SOL price...")
    await refresh_sol_price()

    # Create Telegram client
    client = TelegramClient('snapshot_collector', API_ID, API_HASH)

    @client.on(events.NewMessage(chats=SOURCE_CHANNEL))
    async def handler_main_channel(event):
        """Handler for main source channel"""
        text = event.raw_text or ""
        await process_message(text, SOURCE_CHANNEL)

    @client.on(events.NewMessage(chats=SOURCE_CHANNEL_DEGEN_ONLY))
    async def handler_degen_channel(event):
        """Handler for DEGEN-only source channel"""
        text = event.raw_text or ""
        await process_message(text, SOURCE_CHANNEL_DEGEN_ONLY)

    # Connect to Telegram
    logger.info("🔌 Connecting to Telegram...")
    await client.start(bot_token=BOT_TOKEN)
    logger.info("✅ Connected to Telegram")

    # Get channel info for confirmation
    try:
        main_channel = await client.get_entity(SOURCE_CHANNEL)
        degen_channel = await client.get_entity(SOURCE_CHANNEL_DEGEN_ONLY)
        logger.info(f"👂 Listening to: {getattr(main_channel, 'title', 'Main')} + {getattr(degen_channel, 'title', 'Degen')}")
    except Exception as e:
        logger.warning(f"Could not get channel info: {e}")
        logger.info(f"👂 Listening to channels: {SOURCE_CHANNEL}, {SOURCE_CHANNEL_DEGEN_ONLY}")

    # Start background tasks
    asyncio.create_task(periodic_stats())
    asyncio.create_task(sol_price_refresh_loop())

    logger.info("✅ Snapshot Collector running. Press Ctrl+C to stop.")

    # Run forever
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n👋 Snapshot Collector stopped by user")
        print_stats()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
