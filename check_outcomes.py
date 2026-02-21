#!/usr/bin/env python3
"""
Check Outcomes Script - Run manually after 7+ days

Checks all unchecked tokens, updates their outcomes,
and recalculates wallet statistics.
"""
import asyncio
import logging
import time
from datetime import datetime

from collector import get_current_market_cap
from database import (
    init_database,
    get_unchecked_tokens,
    update_token_outcome,
    update_wallet_stats,
    get_global_stats,
    get_wallet_leaderboard
)

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def check_token(token: dict) -> dict | None:
    """Check a single token's current market cap"""
    contract_address = token["contract_address"]
    symbol = token.get("symbol", "???")
    initial_mc = token.get("api_mc_usd", 0)

    if not initial_mc or initial_mc <= 0:
        logger.warning(f"⚠️ ${symbol}: No initial MC, skipping")
        return None

    current_mc = await get_current_market_cap(contract_address)

    if current_mc is None:
        # Token probably rugged or delisted
        current_mc = 0
        logger.info(f"💀 ${symbol}: Token not found (likely rugged)")

    multiple = current_mc / initial_mc if initial_mc > 0 else 0

    return {
        "contract_address": contract_address,
        "symbol": symbol,
        "wallet_name": token.get("wallet_name"),
        "initial_mc": initial_mc,
        "current_mc": current_mc,
        "multiple": multiple,
        "reached_x2": multiple >= 2,
        "reached_x5": multiple >= 5,
        "reached_x10": multiple >= 10,
    }


async def main():
    """Main entry point"""
    logger.info("🔍 Check Outcomes Script starting...")
    logger.info(f"📅 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Initialize database
    init_database()

    # Get unchecked tokens older than 7 days
    unchecked = get_unchecked_tokens(min_age_seconds=604800)  # 7 days

    if not unchecked:
        logger.info("✅ No tokens to check (all checked or too recent)")
        return

    logger.info(f"📋 Found {len(unchecked)} tokens to check")

    # Process tokens
    results = []
    x2_count = 0
    x5_count = 0
    x10_count = 0

    for i, token in enumerate(unchecked, 1):
        symbol = token.get("symbol", "???")
        logger.info(f"[{i}/{len(unchecked)}] Checking ${symbol}...")

        result = await check_token(token)

        if result:
            results.append(result)

            # Update database
            update_token_outcome(
                result["contract_address"],
                result["current_mc"],
                result["initial_mc"]
            )

            # Track stats
            if result["reached_x2"]:
                x2_count += 1
            if result["reached_x5"]:
                x5_count += 1
            if result["reached_x10"]:
                x10_count += 1

            # Log result
            emoji = "🚀" if result["reached_x2"] else "📉"
            logger.info(
                f"  {emoji} ${symbol} ({result['wallet_name']}): "
                f"${result['initial_mc']:.0f} → ${result['current_mc']:.0f} "
                f"(x{result['multiple']:.2f})"
            )

        # Rate limiting - don't hammer the API
        await asyncio.sleep(0.5)

    # Update wallet stats
    logger.info("\n📊 Updating wallet statistics...")
    update_wallet_stats()

    # Print summary
    print("\n" + "=" * 60)
    print("📊 RÉSUMÉ FINAL")
    print("=" * 60)

    total_checked = len(results)
    winrate = (x2_count / total_checked * 100) if total_checked > 0 else 0

    print(f"\n✅ {total_checked} tokens vérifiés")
    print(f"📈 Winrate global (x2+): {winrate:.1f}%")
    print(f"   - x2+: {x2_count} ({x2_count/total_checked*100:.1f}%)" if total_checked else "")
    print(f"   - x5+: {x5_count} ({x5_count/total_checked*100:.1f}%)" if total_checked else "")
    print(f"   - x10+: {x10_count} ({x10_count/total_checked*100:.1f}%)" if total_checked else "")

    # Wallet leaderboard
    leaderboard = get_wallet_leaderboard()

    if leaderboard:
        print("\n🏆 TOP WALLETS (min 3 signals):")
        print("-" * 40)
        for i, wallet in enumerate(leaderboard[:10], 1):
            print(
                f"  {i}. {wallet['wallet_name']}: "
                f"{wallet['winrate']:.1f}% "
                f"({wallet['total_x2']}/{wallet['total_signals']} x2+)"
            )

    # Global stats
    global_stats = get_global_stats()

    print("\n📈 STATS GLOBALES (toutes périodes):")
    print("-" * 40)
    print(f"  Total tokens: {global_stats.get('total_tokens', 0)}")
    print(f"  Tokens vérifiés: {global_stats.get('checked_tokens', 0)}")

    checked = global_stats.get('checked_tokens', 0)
    if checked > 0:
        print(f"  x2+: {global_stats.get('total_x2', 0)} ({global_stats.get('total_x2', 0)/checked*100:.1f}%)")
        print(f"  x5+: {global_stats.get('total_x5', 0)} ({global_stats.get('total_x5', 0)/checked*100:.1f}%)")
        print(f"  x10+: {global_stats.get('total_x10', 0)} ({global_stats.get('total_x10', 0)/checked*100:.1f}%)")

    print("\n" + "=" * 60)
    print("✅ Check completed!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
