#!/usr/bin/env python3
"""
Check Outcomes Script - Multi-checkpoint verification

Checks tokens at 7 intervals: T+5min, T+20min, T+1h, T+3h, T+6h, T+24h, T+7d
Records market cap at each checkpoint and calculates final results at T+7d.
"""
import asyncio
import argparse
import logging
import time
from datetime import datetime

from collector import fetch_api
from database import (
    init_database,
    get_tokens_for_checkpoint,
    update_token_checkpoint,
    update_wallet_stats,
    get_global_stats,
    get_wallet_leaderboard,
    get_stats_by_day_of_week,
    get_stats_by_hour_range,
    get_stats_by_platform,
    get_stats_by_sol_price,
    get_wallet_stats_detailed,
    CHECKPOINTS,
)
import aiohttp
from config import SOLANA_API_KEY, SOLANA_API_BASE_URL

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Day names for report
DAY_NAMES = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']

# Checkpoint labels for display
CHECKPOINT_LABELS = {
    "5min": "T+5min",
    "20min": "T+20min",
    "1h": "T+1h",
    "3h": "T+3h",
    "6h": "T+6h",
    "24h": "T+24h",
    "7d": "T+7d",
}


async def fetch_token_data(session: aiohttp.ClientSession, contract_address: str) -> dict:
    """
    Fetch token data and ATH in parallel.
    Returns dict with: current_mc, current_ath, holders, liquidity_usd, price_usd, txns_buys, txns_sells
    """
    headers = {"x-api-key": SOLANA_API_KEY}

    async def get_data(endpoint):
        try:
            url = f"{SOLANA_API_BASE_URL}{endpoint}"
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.json()
        except Exception as e:
            logger.warning(f"API error {endpoint}: {e}")
        return None

    token_data, ath_data = await asyncio.gather(
        get_data(f"/tokens/{contract_address}"),
        get_data(f"/tokens/{contract_address}/ath")
    )

    result = {
        "current_mc": 0,
        "current_ath": 0,
        "holders": None,
        "liquidity_usd": None,
        "price_usd": None,
        "txns_buys": None,
        "txns_sells": None,
    }

    # Extract data from /tokens/{token}
    if token_data:
        # Holders (top level)
        result["holders"] = token_data.get("holders")

        # Pool data
        pools = token_data.get("pools", [])
        if pools:
            active_pools = [p for p in pools if isinstance(p, dict) and (p.get("marketCap", {}).get("usd") or 0) > 0]
            if active_pools:
                active_pools.sort(key=lambda p: p.get("marketCap", {}).get("usd", 0), reverse=True)
                best_pool = active_pools[0]

                # Market cap
                result["current_mc"] = best_pool.get("marketCap", {}).get("usd", 0)

                # Liquidity
                result["liquidity_usd"] = best_pool.get("liquidity", {}).get("usd")

                # Price
                result["price_usd"] = best_pool.get("price", {}).get("usd")

                # Transactions
                txns = best_pool.get("txns", {})
                buys = txns.get("buys")
                sells = txns.get("sells")
                result["txns_buys"] = buys.get("total") if isinstance(buys, dict) else buys
                result["txns_sells"] = sells.get("total") if isinstance(sells, dict) else sells

    # Extract current ATH from /tokens/{token}/ath
    if ath_data and isinstance(ath_data, dict):
        result["current_ath"] = ath_data.get("highest_market_cap") or ath_data.get("marketCap", {}).get("usd") or 0

    return result


async def check_token(session: aiohttp.ClientSession, token: dict, checkpoint: str) -> dict | None:
    """Check a single token at a checkpoint - extracts MC, holders, liquidity, price, buys, sells"""
    contract_address = token["contract_address"]
    symbol = token.get("symbol", "???")
    mc_at_call = token.get("api_mc_usd", 0) or 0
    wallet_name = token.get("wallet_name", "?")

    if not mc_at_call or mc_at_call <= 0:
        logger.warning(f"⚠️ ${symbol}: No initial MC, skipping")
        return None

    # Fetch current data (all fields from same API call)
    data = await fetch_token_data(session, contract_address)

    current_mc = data["current_mc"]
    current_ath = data["current_ath"]

    if current_mc == 0:
        logger.info(f"💀 ${symbol}: Token not found (likely rugged)")

    # Update database with all checkpoint data
    result = update_token_checkpoint(
        contract_address=contract_address,
        checkpoint=checkpoint,
        current_mc=current_mc,
        current_ath=current_ath,
        mc_at_call=mc_at_call,
        holders=data["holders"],
        liquidity_usd=data["liquidity_usd"],
        price_usd=data["price_usd"],
        txns_buys=data["txns_buys"],
        txns_sells=data["txns_sells"]
    )

    result["contract_address"] = contract_address
    result["symbol"] = symbol
    result["wallet_name"] = wallet_name
    result["mc_at_call"] = mc_at_call

    # Log result with enriched data
    if checkpoint == "7d":
        multiple = result.get("true_multiple", 0)
        if result.get("reached_x2"):
            emoji = "🚀"
        elif current_mc == 0:
            emoji = "💀"
        else:
            emoji = "📉"
        logger.info(
            f"  {emoji} ${symbol} ({wallet_name}): "
            f"${mc_at_call:.0f} → ${current_mc:.0f} (x{multiple:.2f}) | "
            f"H:{data['holders'] or '?'} L:${data['liquidity_usd'] or 0:.0f}"
        )
    else:
        change = ((current_mc / mc_at_call) - 1) * 100 if mc_at_call > 0 else 0
        emoji = "📈" if change > 0 else "📉"
        holders_info = f"H:{data['holders']}" if data['holders'] else ""
        logger.info(f"  {emoji} ${symbol}: ${mc_at_call:.0f} → ${current_mc:.0f} ({change:+.1f}%) {holders_info}")

    return result


async def run_checkpoint(checkpoint: str):
    """Run checks for a specific checkpoint"""
    label = CHECKPOINT_LABELS.get(checkpoint, checkpoint)
    logger.info(f"\n{'='*60}")
    logger.info(f"🔍 Running {label} checkpoint...")
    logger.info(f"📅 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Initialize database
    init_database()

    # Get tokens to check
    tokens = get_tokens_for_checkpoint(checkpoint)

    if not tokens:
        logger.info(f"✅ No tokens to check for {label}")
        return [], 0

    logger.info(f"📋 Found {len(tokens)} tokens for {label} checkpoint")

    # Process tokens
    results = []
    processed_count = 0

    async with aiohttp.ClientSession() as session:
        for i, token in enumerate(tokens, 1):
            symbol = token.get("symbol", "???")
            logger.info(f"[{i}/{len(tokens)}] Checking ${symbol}...")

            result = await check_token(session, token, checkpoint)

            if result:
                results.append(result)
                processed_count += 1

            # Rate limiting
            await asyncio.sleep(0.3)

    logger.info(f"✅ {label} checkpoint complete: {processed_count} tokens processed")

    return results, processed_count


def print_report():
    """Print comprehensive statistics report"""
    print("\n" + "=" * 70)
    print("📊 RAPPORT COMPLET")
    print("=" * 70)

    # Global stats
    stats = get_global_stats()
    total = stats.get('total_tokens', 0)
    checked = stats.get('checked_tokens', 0)
    excluded = stats.get('excluded_tokens', 0)
    included = stats.get('included_tokens', 0)
    x2 = stats.get('total_x2', 0)
    x5 = stats.get('total_x5', 0)
    x10 = stats.get('total_x10', 0)

    print(f"\n✅ {checked} tokens vérifiés (sur {total} total)")
    print(f"⚠️  {excluded} tokens exclus (ath_ratio < 0.5)")
    print(f"📋 {included} tokens inclus dans l'analyse")

    if included > 0:
        print(f"\n📊 Winrate x2 (tokens inclus) : {x2/included*100:.1f}%")
        print(f"📊 Winrate x5 : {x5/included*100:.1f}%")
        print(f"📊 Winrate x10 : {x10/included*100:.1f}%")

    # Wallet stats
    wallet_stats = get_wallet_stats_detailed()
    if wallet_stats:
        print("\n" + "-" * 70)
        print("🏆 PAR WALLET (min 3 calls) :")
        print("-" * 70)
        for w in wallet_stats[:15]:
            winrate = w['x2_count'] / w['total'] * 100 if w['total'] > 0 else 0
            platform = w.get('dominant_platform', '?')
            if platform and 'pump.fun' in str(platform):
                platform = 'pump.fun'
            elif platform and 'bonk' in str(platform).lower():
                platform = 'letsbonk'
            print(f"  {w['wallet_name']:15} : {winrate:5.1f}% winrate | {w['total']:3} calls | {platform}")

    # Day of week stats
    day_stats = get_stats_by_day_of_week()
    if day_stats:
        print("\n" + "-" * 70)
        print("📅 PAR JOUR UTC :")
        print("-" * 70)
        day_results = {}
        for d in day_stats:
            day_num = d['day_of_week']
            total = d['total']
            x2_count = d['x2_count']
            winrate = x2_count / total * 100 if total > 0 else 0
            day_results[day_num] = (winrate, total)

        for i, day_name in enumerate(DAY_NAMES):
            if i in day_results:
                wr, cnt = day_results[i]
                print(f"  {day_name:10} : {wr:5.1f}% ({cnt} calls)")
            else:
                print(f"  {day_name:10} : - (0 calls)")

    # Hour range stats
    hour_stats = get_stats_by_hour_range()
    if hour_stats:
        print("\n" + "-" * 70)
        print("⏰ PAR TRANCHE HORAIRE UTC :")
        print("-" * 70)
        for h in hour_stats:
            start = h['hour_range']
            end = start + 4
            total = h['total']
            x2_count = h['x2_count']
            winrate = x2_count / total * 100 if total > 0 else 0
            print(f"  {start:02d}h-{end:02d}h : {winrate:5.1f}% winrate ({total} calls)")

    # Platform stats
    platform_stats = get_stats_by_platform()
    if platform_stats:
        print("\n" + "-" * 70)
        print("🏭 PAR PLATEFORME :")
        print("-" * 70)
        for p in platform_stats:
            total = p['total']
            x2_count = p['x2_count']
            winrate = x2_count / total * 100 if total > 0 else 0
            name = p['platform_name'] or 'unknown'
            if len(name) > 20:
                name = name[:20] + "..."
            print(f"  {name:20} : {winrate:5.1f}% winrate ({total} calls)")

    # SOL price stats
    sol_stats = get_stats_by_sol_price()
    if sol_stats:
        print("\n" + "-" * 70)
        print("💰 PAR PRIX SOL :")
        print("-" * 70)
        for s in sol_stats:
            total = s['total']
            x2_count = s['x2_count']
            winrate = x2_count / total * 100 if total > 0 else 0
            print(f"  {s['sol_range']:10} : {winrate:5.1f}% winrate ({total} calls)")

    print("\n" + "=" * 70)
    print("✅ Rapport terminé!")
    print("=" * 70)


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Check token outcomes at checkpoints')
    parser.add_argument('--checkpoint', '-c', type=str,
                        choices=list(CHECKPOINTS.keys()),
                        help='Specific checkpoint to run (5min, 20min, 1h, 3h, 6h, 24h, 7d)')
    parser.add_argument('--all', action='store_true',
                        help='Run all checkpoints')
    parser.add_argument('--report-only', action='store_true',
                        help='Only print report, no API calls')

    args = parser.parse_args()

    logger.info("🔍 Check Outcomes Script starting...")

    # Initialize database
    init_database()

    if args.report_only:
        print_report()
        return

    if args.all:
        # Run all checkpoints
        for checkpoint in CHECKPOINTS.keys():
            await run_checkpoint(checkpoint)
    elif args.checkpoint:
        # Run specific checkpoint
        await run_checkpoint(args.checkpoint)
    else:
        # Default: run all checkpoints that have pending tokens
        for checkpoint in CHECKPOINTS.keys():
            await run_checkpoint(checkpoint)

    # Update wallet stats after 7d checkpoint
    logger.info("\n📊 Updating wallet statistics...")
    update_wallet_stats()

    # Print report
    print_report()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise
