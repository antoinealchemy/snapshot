#!/usr/bin/env python3
"""
Check Outcomes Script - Multi-day verification (J+1, J+3, J+7)

Checks tokens at multiple intervals, uses ATH comparison for accurate
multiple detection, and generates comprehensive reports.
"""
import asyncio
import argparse
import logging
import time
from datetime import datetime

from collector import get_current_market_cap, fetch_api
from database import (
    init_database,
    get_tokens_for_check,
    update_token_check,
    update_wallet_stats,
    get_global_stats,
    get_wallet_leaderboard,
    get_stats_by_day_of_week,
    get_stats_by_hour_range,
    get_stats_by_platform,
    get_stats_by_sol_price,
    get_wallet_stats_detailed,
    get_detection_type_stats,
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


async def fetch_token_data(session: aiohttp.ClientSession, contract_address: str) -> tuple:
    """Fetch both token data and ATH in parallel"""
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

    # Extract current MC
    current_mc = 0
    if token_data:
        pools = token_data.get("pools", [])
        if pools:
            active_pools = [p for p in pools if isinstance(p, dict) and (p.get("marketCap", {}).get("usd") or 0) > 0]
            if active_pools:
                active_pools.sort(key=lambda p: p.get("marketCap", {}).get("usd", 0), reverse=True)
                current_mc = active_pools[0].get("marketCap", {}).get("usd", 0)

    # Extract current ATH
    current_ath = 0
    if ath_data and isinstance(ath_data, dict):
        current_ath = ath_data.get("highest_market_cap") or ath_data.get("marketCap", {}).get("usd") or 0

    return current_mc, current_ath


async def check_token(session: aiohttp.ClientSession, token: dict, check_day: int) -> dict | None:
    """Check a single token's outcome"""
    contract_address = token["contract_address"]
    symbol = token.get("symbol", "???")
    mc_at_call = token.get("api_mc_usd", 0) or 0
    ath_at_call = token.get("ath_market_cap", 0) or 0
    excluded = token.get("excluded_from_analysis", 0)
    wallet_name = token.get("wallet_name", "?")

    if not mc_at_call or mc_at_call <= 0:
        logger.warning(f"⚠️ ${symbol}: No initial MC, skipping")
        return None

    # Fetch current data (2 API calls in parallel)
    current_mc, current_ath = await fetch_token_data(session, contract_address)

    if current_mc == 0 and current_ath == 0:
        # Token probably rugged or delisted
        logger.info(f"💀 ${symbol}: Token not found (likely rugged)")
        current_mc = 0
        current_ath = 0

    # Update database with check result
    result = update_token_check(
        contract_address=contract_address,
        check_day=check_day,
        current_mc=current_mc,
        current_ath=current_ath,
        ath_at_call=ath_at_call,
        mc_at_call=mc_at_call
    )

    result["contract_address"] = contract_address
    result["symbol"] = symbol
    result["wallet_name"] = wallet_name
    result["excluded"] = excluded
    result["mc_at_call"] = mc_at_call
    result["current_mc"] = current_mc
    result["current_ath"] = current_ath
    result["ath_at_call"] = ath_at_call

    # Log result
    if excluded:
        emoji = "⚠️"
        status = "EXCLUDED"
    elif result["detection_type"] == "ATH_CONFIRMED":
        emoji = "🎯"
        status = "ATH_CONFIRMED"
    elif result["reached_x2"]:
        emoji = "🚀"
        status = "x2+"
    else:
        emoji = "📉"
        status = "APPROX"

    logger.info(
        f"  {emoji} ${symbol} ({wallet_name}): "
        f"${mc_at_call:.0f} → MC:${current_mc:.0f} ATH:${current_ath:.0f} "
        f"(x{result['true_multiple']:.2f}) [{status}]"
    )

    return result


async def run_check(check_day: int):
    """Run checks for a specific day (1, 3, or 7)"""
    logger.info(f"\n{'='*60}")
    logger.info(f"🔍 Running J+{check_day} check...")
    logger.info(f"📅 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Initialize database
    init_database()

    # Get tokens to check
    tokens = get_tokens_for_check(check_day)

    if not tokens:
        logger.info(f"✅ No tokens to check for J+{check_day}")
        return [], 0, 0

    logger.info(f"📋 Found {len(tokens)} tokens for J+{check_day} check")

    # Process tokens
    results = []
    included_count = 0
    excluded_count = 0

    async with aiohttp.ClientSession() as session:
        for i, token in enumerate(tokens, 1):
            symbol = token.get("symbol", "???")
            logger.info(f"[{i}/{len(tokens)}] Checking ${symbol}...")

            result = await check_token(session, token, check_day)

            if result:
                results.append(result)
                if result["excluded"]:
                    excluded_count += 1
                else:
                    included_count += 1

            # Rate limiting
            await asyncio.sleep(0.3)

    logger.info(f"✅ J+{check_day} check complete: {included_count} included, {excluded_count} excluded")

    return results, included_count, excluded_count


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

    # Detection type stats
    detection_stats = get_detection_type_stats()
    total_detected = sum(detection_stats.values()) if detection_stats else 0
    if total_detected > 0:
        ath_confirmed = detection_stats.get('ATH_CONFIRMED', 0)
        approximation = detection_stats.get('APPROXIMATION', 0)
        print(f"\n🔍 ATH_CONFIRMED : {ath_confirmed} ({ath_confirmed/total_detected*100:.1f}%)")
        print(f"🔍 APPROXIMATION : {approximation} ({approximation/total_detected*100:.1f}%)")

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
    parser = argparse.ArgumentParser(description='Check token outcomes')
    parser.add_argument('--day', type=int, choices=[1, 3, 7], default=7,
                        help='Check day (1, 3, or 7). Default: 7')
    parser.add_argument('--all', action='store_true',
                        help='Run all checks (J+1, J+3, J+7)')
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
        # Run all checks
        for day in [1, 3, 7]:
            await run_check(day)
    else:
        # Run single check
        await run_check(args.day)

    # Update wallet stats
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
