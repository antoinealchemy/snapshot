"""
Solana Tracker API collector for token snapshots
"""
import asyncio
import aiohttp
import time
import logging
from datetime import datetime

from config import SOLANA_API_KEY, SOLANA_API_BASE_URL

logger = logging.getLogger(__name__)

# API settings
API_TIMEOUT = 5  # seconds

# SOL Token address
SOL_TOKEN_ADDRESS = "So11111111111111111111111111111111111111112"

# SOL Price Cache
_sol_price_cache = {
    "price": None,
    "last_updated": 0,
    "refresh_interval": 6 * 60 * 60,  # 6 hours in seconds
}


async def fetch_api(session: aiohttp.ClientSession, endpoint: str) -> dict | None:
    """Fetch data from Solana Tracker API with timeout"""
    url = f"{SOLANA_API_BASE_URL}{endpoint}"
    headers = {"x-api-key": SOLANA_API_KEY}

    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=API_TIMEOUT)) as response:
            if response.status == 200:
                return await response.json()
            else:
                logger.warning(f"API {endpoint} returned {response.status}")
                return None
    except asyncio.TimeoutError:
        logger.warning(f"API {endpoint} timeout after {API_TIMEOUT}s")
        return None
    except Exception as e:
        logger.error(f"API {endpoint} error: {e}")
        return None


async def fetch_sol_price() -> float | None:
    """Fetch current SOL price from API"""
    async with aiohttp.ClientSession() as session:
        data = await fetch_api(session, f"/tokens/{SOL_TOKEN_ADDRESS}")

        if not data:
            return None

        pools = data.get("pools", [])
        if pools and isinstance(pools[0], dict):
            price = pools[0].get("price", {}).get("usd")
            return price

        return None


async def refresh_sol_price():
    """Refresh SOL price and update cache"""
    try:
        price = await fetch_sol_price()

        if price is not None:
            _sol_price_cache["price"] = price
            _sol_price_cache["last_updated"] = time.time()
            logger.info(f"💰 SOL price updated: ${price:.2f}")

            # Save to history (import here to avoid circular import)
            from database import save_sol_price_history
            period_label = datetime.now().strftime("%Y-%m-%d %H:00")
            save_sol_price_history(price, period_label)

            return price
        else:
            logger.warning("⚠️ Could not fetch SOL price, keeping last known value")
            return _sol_price_cache["price"]

    except Exception as e:
        logger.error(f"Error refreshing SOL price: {e}")
        return _sol_price_cache["price"]


def get_sol_price() -> float | None:
    """Get cached SOL price (no API call)"""
    return _sol_price_cache["price"]


async def sol_price_refresh_loop():
    """Background task to refresh SOL price every 6 hours"""
    while True:
        await refresh_sol_price()
        await asyncio.sleep(_sol_price_cache["refresh_interval"])


async def collect_snapshot(contract_address: str) -> dict:
    """
    Collect complete snapshot for a token via 5 parallel API calls.
    Returns dict ready for database insertion.
    """
    async with aiohttp.ClientSession() as session:
        # 5 appels en parallèle (incluant SOL price)
        token_task = fetch_api(session, f"/tokens/{contract_address}")
        ath_task = fetch_api(session, f"/tokens/{contract_address}/ath")
        stats_task = fetch_api(session, f"/stats/{contract_address}")
        first_buyers_task = fetch_api(session, f"/first-buyers/{contract_address}")
        sol_price_task = fetch_api(session, f"/tokens/{SOL_TOKEN_ADDRESS}")

        token_data, ath_data, stats_data, first_buyers_data, sol_data = await asyncio.gather(
            token_task, ath_task, stats_task, first_buyers_task, sol_price_task
        )

    # Construire le snapshot
    snapshot = {
        "contract_address": contract_address,
        "first_detected_at": int(time.time()),
    }

    # Parser /tokens/{token}
    if token_data:
        snapshot.update(parse_token_data(token_data))

    # Parser /tokens/{token}/ath
    if ath_data:
        snapshot.update(parse_ath_data(ath_data, snapshot.get("api_mc_usd")))

    # Parser /stats/{token}
    if stats_data:
        snapshot.update(parse_stats_data(stats_data))

    # Parser /first-buyers/{token}
    if first_buyers_data:
        snapshot.update(parse_first_buyers_data(first_buyers_data))

    # SOL price: real-time from API, fallback to cache
    sol_price = None
    if sol_data:
        pools = sol_data.get("pools", [])
        if pools and isinstance(pools[0], dict):
            sol_price = pools[0].get("price", {}).get("usd")

    # Fallback to cached price if API call failed
    if sol_price is None:
        sol_price = get_sol_price()
        if sol_price:
            logger.debug(f"SOL price from cache: ${sol_price:.2f}")

    snapshot["sol_price_at_signal"] = sol_price

    return snapshot


def parse_token_data(data: dict) -> dict:
    """Extract fields from /tokens/{token} response"""
    result = {}

    # Token info
    token = data.get("token", {})
    result["symbol"] = token.get("symbol")
    result["platform"] = token.get("createdOn")

    # Pools - trouver le meilleur pool (plus grosse MC)
    pools = data.get("pools", [])
    if pools:
        active_pools = [p for p in pools if isinstance(p, dict) and (p.get("marketCap", {}).get("usd") or 0) > 0]
        if active_pools:
            active_pools.sort(key=lambda p: p.get("marketCap", {}).get("usd", 0), reverse=True)
            best_pool = active_pools[0]

            result["api_mc_usd"] = best_pool.get("marketCap", {}).get("usd")
            result["api_liquidity_usd"] = best_pool.get("liquidity", {}).get("usd")
            result["api_liquidity_sol"] = best_pool.get("liquidity", {}).get("quote")
            # lpBurn can be True/False or a percentage (100 = fully burned)
            lp_burn = best_pool.get("lpBurn")
            if isinstance(lp_burn, bool):
                result["lp_burn"] = 1 if lp_burn else 0
            elif isinstance(lp_burn, (int, float)):
                result["lp_burn"] = 1 if lp_burn >= 100 else 0
            else:
                result["lp_burn"] = 0
            result["curve_percentage"] = best_pool.get("curvePercentage")

            # Transactions (can be int or dict with "total" key)
            txns = best_pool.get("txns", {})
            buys = txns.get("buys")
            sells = txns.get("sells")
            result["txns_buys_total"] = buys.get("total") if isinstance(buys, dict) else buys
            result["txns_sells_total"] = sells.get("total") if isinstance(sells, dict) else sells

    # Holders
    result["holders"] = data.get("holders")

    # Token age
    created_timestamp = token.get("createdAt")
    if created_timestamp:
        try:
            age_seconds = int(time.time()) - int(created_timestamp / 1000)  # API gives ms
            result["token_age_minutes"] = age_seconds // 60
        except (ValueError, TypeError):
            pass

    # Risk data
    risk = data.get("risk", {})
    result["risk_score"] = risk.get("score")
    result["risk_rugged"] = 1 if risk.get("rugged") else 0
    result["risk_top10"] = risk.get("top10HoldersPercent")

    # Risk details from risks array
    risks = risk.get("risks", [])
    for r in risks:
        name = r.get("name", "").lower()
        value = r.get("value")

        # Parse Top 10 Holders from risks array if not directly available
        if "top 10" in name and result["risk_top10"] is None:
            if isinstance(value, str) and "%" in value:
                try:
                    result["risk_top10"] = float(value.replace("%", ""))
                except ValueError:
                    pass
            elif isinstance(value, (int, float)):
                result["risk_top10"] = value

        if "sniper" in name:
            result["risk_snipers"] = value if isinstance(value, int) else None
        elif "insider" in name.lower():
            result["risk_insiders"] = value
        elif "dev" in name.lower() and "percent" in name.lower():
            result["risk_dev_pct"] = value

    return result


def parse_ath_data(data: dict, current_mc: float | None) -> dict:
    """Extract fields from /tokens/{token}/ath response"""
    result = {}

    # ATH peut être directement dans data ou dans data.highest_market_cap
    ath_mc = None

    if isinstance(data, dict):
        # Format 1: {"highest_market_cap": 123456}
        ath_mc = data.get("highest_market_cap")

        # Format 2: nested
        if not ath_mc and "ath" in data:
            ath_mc = data.get("ath", {}).get("market_cap")

    if ath_mc:
        result["ath_market_cap"] = ath_mc
        if current_mc and ath_mc > 0:
            result["ath_ratio"] = current_mc / ath_mc

    return result


def parse_stats_data(data: dict) -> dict:
    """Extract fields from /stats/{token} response"""
    result = {}

    # Format peut varier - chercher dans plusieurs endroits
    stats = data

    # 5 minutes
    m5 = stats.get("5m", {})
    if isinstance(m5, dict):
        volume_5m = m5.get("volume")
        # volume can be a dict with {buys, sells, total} or a direct number
        if isinstance(volume_5m, dict):
            result["volume_5m_usd"] = volume_5m.get("total")
        else:
            result["volume_5m_usd"] = volume_5m
        result["buyers_5m"] = m5.get("buyers")
        result["sellers_5m"] = m5.get("sellers")

    # 1 hour
    h1 = stats.get("1h", {})
    if isinstance(h1, dict):
        volume_1h = h1.get("volume")
        if isinstance(volume_1h, dict):
            result["volume_1h_usd"] = volume_1h.get("total")
        else:
            result["volume_1h_usd"] = volume_1h
        result["buyers_1h"] = h1.get("buyers")
        result["sellers_1h"] = h1.get("sellers")

    return result


def parse_first_buyers_data(data: dict | list) -> dict:
    """Extract fields from /first-buyers/{token} response"""
    result = {}

    buyers = []
    if isinstance(data, list):
        buyers = data
    elif isinstance(data, dict):
        buyers = data.get("buyers", []) or data.get("data", [])

    if buyers:
        result["early_buyers_count"] = len(buyers)

        # Compter ceux qui hold encore
        still_holding = 0
        total_pnl = 0
        pnl_count = 0

        for buyer in buyers:
            if isinstance(buyer, dict):
                # Check if still holding
                current_value = buyer.get("currentValue", 0) or 0
                if current_value > 0:
                    still_holding += 1

                # PnL
                pnl = buyer.get("pnl")
                if pnl is not None:
                    total_pnl += pnl
                    pnl_count += 1

        result["early_buyers_still_holding"] = still_holding
        result["early_buyers_avg_pnl"] = (total_pnl / pnl_count) if pnl_count > 0 else None

    return result


async def get_current_market_cap(contract_address: str) -> float | None:
    """Get current market cap for a token (used in check_outcomes)"""
    async with aiohttp.ClientSession() as session:
        data = await fetch_api(session, f"/tokens/{contract_address}")

        if not data:
            return None

        pools = data.get("pools", [])
        if pools:
            active_pools = [p for p in pools if isinstance(p, dict) and (p.get("marketCap", {}).get("usd") or 0) > 0]
            if active_pools:
                active_pools.sort(key=lambda p: p.get("marketCap", {}).get("usd", 0), reverse=True)
                return active_pools[0].get("marketCap", {}).get("usd")

        return None
