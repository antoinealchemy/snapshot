"""
SQLite database operations for token snapshots
"""
import sqlite3
import os
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "snapshots.db")


def calculate_time_fields(timestamp: int) -> dict:
    """Calculate time-based fields from unix timestamp"""
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return {
        "day_of_week": dt.weekday(),  # 0=Monday, 6=Sunday
        "hour_utc": dt.hour,
        "week_number": dt.isocalendar()[1],
        "month": dt.month,
    }


def get_connection():
    """Get SQLite connection with row factory"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Initialize database with required tables"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS token_snapshots (
            -- Identifiants
            contract_address          TEXT PRIMARY KEY,
            symbol                    TEXT,
            first_detected_at         INTEGER,
            wallet_name               TEXT,
            wallet_address            TEXT,
            source_channel            TEXT,

            -- Données du signal (depuis le message Telegram)
            signal_mc_usd             REAL,
            signal_lq_usd             REAL,
            seen_minutes              INTEGER,

            -- /tokens/{token} - 1 appel
            api_mc_usd                REAL,
            api_liquidity_usd         REAL,
            api_liquidity_sol         REAL,
            holders                   INTEGER,
            curve_percentage          REAL,
            lp_burn                   INTEGER,
            token_age_minutes         INTEGER,
            platform                  TEXT,
            risk_score                INTEGER,
            risk_top10                REAL,
            risk_snipers              INTEGER,
            risk_insiders             INTEGER,
            risk_dev_pct              REAL,
            txns_buys_total           INTEGER,
            txns_sells_total          INTEGER,
            price_change_5m           REAL,
            price_change_1h           REAL,

            -- /tokens/{token}/ath - 1 appel
            ath_market_cap            REAL,
            ath_ratio                 REAL,

            -- /stats/{token} - 1 appel
            volume_5m_usd             REAL,
            buyers_5m                 INTEGER,
            sellers_5m                INTEGER,
            volume_1h_usd             REAL,
            buyers_1h                 INTEGER,
            sellers_1h                INTEGER,

            -- Prix SOL au moment du signal
            sol_price_at_signal           REAL,

            -- Champs temporels (calculés depuis first_detected_at)
            day_of_week               INTEGER,
            hour_utc                  INTEGER,
            week_number               INTEGER,
            month                     INTEGER,

            -- Exclusion de l'analyse (ath_ratio < 0.5)
            excluded_from_analysis    INTEGER DEFAULT 0,

            -- Checkpoints de suivi (données à chaque timestamp)
            -- T+5min
            mc_5min                   REAL,
            ath_5min                  REAL,
            holders_5min              INTEGER,
            liquidity_5min            REAL,
            price_5min                REAL,
            buys_5min                 INTEGER,
            sells_5min                INTEGER,
            -- T+20min
            mc_20min                  REAL,
            ath_20min                 REAL,
            holders_20min             INTEGER,
            liquidity_20min           REAL,
            price_20min               REAL,
            buys_20min                INTEGER,
            sells_20min               INTEGER,
            -- T+1h
            mc_1h                     REAL,
            ath_1h                    REAL,
            holders_1h                INTEGER,
            liquidity_1h              REAL,
            price_1h                  REAL,
            buys_1h                   INTEGER,
            sells_1h                  INTEGER,
            -- T+3h
            mc_3h                     REAL,
            ath_3h                    REAL,
            holders_3h                INTEGER,
            liquidity_3h              REAL,
            price_3h                  REAL,
            buys_3h                   INTEGER,
            sells_3h                  INTEGER,
            -- T+6h
            mc_6h                     REAL,
            ath_6h                    REAL,
            holders_6h                INTEGER,
            liquidity_6h              REAL,
            price_6h                  REAL,
            buys_6h                   INTEGER,
            sells_6h                  INTEGER,
            -- T+24h
            mc_24h                    REAL,
            ath_24h                   REAL,
            holders_24h               INTEGER,
            liquidity_24h             REAL,
            price_24h                 REAL,
            buys_24h                  INTEGER,
            sells_24h                 INTEGER,
            -- T+7d
            mc_7d                     REAL,
            ath_7d                    REAL,
            holders_7d                INTEGER,
            liquidity_7d              REAL,
            price_7d                  REAL,
            buys_7d                   INTEGER,
            sells_7d                  INTEGER,

            -- Résultats (mis à jour à chaque checkpoint)
            current_ath_usd           REAL,
            true_multiple             REAL,
            reached_x2                INTEGER,
            reached_x3                INTEGER,
            reached_x5                INTEGER,
            reached_x10               INTEGER,
            reached_x20               INTEGER,
            reached_x50               INTEGER,
            reached_x100              INTEGER
        )
    """)

    # Migration: add new columns if they don't exist
    _migrate_add_columns(cursor)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wallet_stats (
            wallet_name     TEXT PRIMARY KEY,
            wallet_address  TEXT,
            total_signals   INTEGER DEFAULT 0,
            total_x2        INTEGER DEFAULT 0,
            winrate         REAL,
            last_updated    INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sol_price_history (
            timestamp       INTEGER PRIMARY KEY,
            sol_price       REAL,
            period_label    TEXT
        )
    """)

    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


def _migrate_add_columns(cursor):
    """Add new columns to existing tables if they don't exist"""
    # Get existing columns
    cursor.execute("PRAGMA table_info(token_snapshots)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    # New columns to add
    new_columns = [
        ("day_of_week", "INTEGER"),
        ("hour_utc", "INTEGER"),
        ("week_number", "INTEGER"),
        ("month", "INTEGER"),
        ("excluded_from_analysis", "INTEGER DEFAULT 0"),
        # Checkpoint columns - mc
        ("mc_5min", "REAL"),
        ("mc_20min", "REAL"),
        ("mc_1h", "REAL"),
        ("mc_3h", "REAL"),
        ("mc_6h", "REAL"),
        ("mc_24h", "REAL"),
        ("mc_7d", "REAL"),
        # Checkpoint columns - ath
        ("ath_5min", "REAL"),
        ("ath_20min", "REAL"),
        ("ath_1h", "REAL"),
        ("ath_3h", "REAL"),
        ("ath_6h", "REAL"),
        ("ath_24h", "REAL"),
        ("ath_7d", "REAL"),
        # Checkpoint columns - holders
        ("holders_5min", "INTEGER"),
        ("holders_20min", "INTEGER"),
        ("holders_1h", "INTEGER"),
        ("holders_3h", "INTEGER"),
        ("holders_6h", "INTEGER"),
        ("holders_24h", "INTEGER"),
        ("holders_7d", "INTEGER"),
        # Checkpoint columns - liquidity
        ("liquidity_5min", "REAL"),
        ("liquidity_20min", "REAL"),
        ("liquidity_1h", "REAL"),
        ("liquidity_3h", "REAL"),
        ("liquidity_6h", "REAL"),
        ("liquidity_24h", "REAL"),
        ("liquidity_7d", "REAL"),
        # Checkpoint columns - price
        ("price_5min", "REAL"),
        ("price_20min", "REAL"),
        ("price_1h", "REAL"),
        ("price_3h", "REAL"),
        ("price_6h", "REAL"),
        ("price_24h", "REAL"),
        ("price_7d", "REAL"),
        # Checkpoint columns - buys
        ("buys_5min", "INTEGER"),
        ("buys_20min", "INTEGER"),
        ("buys_1h", "INTEGER"),
        ("buys_3h", "INTEGER"),
        ("buys_6h", "INTEGER"),
        ("buys_24h", "INTEGER"),
        ("buys_7d", "INTEGER"),
        # Checkpoint columns - sells
        ("sells_5min", "INTEGER"),
        ("sells_20min", "INTEGER"),
        ("sells_1h", "INTEGER"),
        ("sells_3h", "INTEGER"),
        ("sells_6h", "INTEGER"),
        ("sells_24h", "INTEGER"),
        ("sells_7d", "INTEGER"),
        # Results (updated at each checkpoint)
        ("current_ath_usd", "REAL"),
        ("true_multiple", "REAL"),
        ("reached_x3", "INTEGER"),
        ("reached_x20", "INTEGER"),
        ("reached_x50", "INTEGER"),
        ("reached_x100", "INTEGER"),
    ]

    # Columns to remove (if they exist, we'll just ignore them)
    deprecated_columns = ["checked_j1", "checked_j3", "checked_j7", "detection_type",
                          "risk_rugged", "early_buyers_still_holding", "early_buyers_avg_pnl",
                          "early_buyers_count", "max_mc_reached"]

    for col_name, col_type in new_columns:
        if col_name not in existing_columns:
            try:
                cursor.execute(f"ALTER TABLE token_snapshots ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added column {col_name} to token_snapshots")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    logger.warning(f"Could not add column {col_name}: {e}")


def save_sol_price_history(sol_price: float, period_label: str):
    """Save SOL price to history table"""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT OR REPLACE INTO sol_price_history (timestamp, sol_price, period_label) VALUES (?, ?, ?)",
            (int(time.time()), sol_price, period_label)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error saving SOL price history: {e}")
    finally:
        conn.close()


def token_exists(contract_address: str) -> bool:
    """Check if token already exists in database"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM token_snapshots WHERE contract_address = ?",
        (contract_address,)
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def insert_snapshot(data: dict) -> bool:
    """Insert a new token snapshot. Returns True if inserted, False if already exists."""
    conn = get_connection()
    cursor = conn.cursor()

    # BUG 1 FIX: ALWAYS calculate time fields from first_detected_at
    # Ensure first_detected_at exists
    if "first_detected_at" not in data or not data["first_detected_at"]:
        data["first_detected_at"] = int(time.time())

    # Always calculate and set time fields
    time_fields = calculate_time_fields(data["first_detected_at"])
    data["day_of_week"] = time_fields["day_of_week"]
    data["hour_utc"] = time_fields["hour_utc"]
    data["week_number"] = time_fields["week_number"]
    data["month"] = time_fields["month"]

    logger.debug(f"Time fields: day={time_fields['day_of_week']}, hour={time_fields['hour_utc']}")

    # Calculate excluded_from_analysis based on ath_ratio
    api_mc = data.get("api_mc_usd", 0) or 0
    ath_mc = data.get("ath_market_cap", 0) or 0
    if ath_mc > 0:
        ath_ratio = api_mc / ath_mc
        data["ath_ratio"] = ath_ratio
        data["excluded_from_analysis"] = 1 if ath_ratio < 0.5 else 0
    else:
        data["excluded_from_analysis"] = 1  # No ATH = exclude

    columns = list(data.keys())
    placeholders = ", ".join(["?" for _ in columns])
    columns_str = ", ".join(columns)

    try:
        cursor.execute(
            f"INSERT OR IGNORE INTO token_snapshots ({columns_str}) VALUES ({placeholders})",
            list(data.values())
        )
        conn.commit()
        inserted = cursor.rowcount > 0
        conn.close()
        return inserted
    except Exception as e:
        logger.error(f"Error inserting snapshot: {e}")
        conn.close()
        return False


# Checkpoint definitions: name -> seconds delay
CHECKPOINTS = {
    "5min": 5 * 60,
    "20min": 20 * 60,
    "1h": 60 * 60,
    "3h": 3 * 60 * 60,
    "6h": 6 * 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
}


def get_tokens_for_checkpoint(checkpoint: str) -> list:
    """
    Get tokens ready for a specific checkpoint.
    checkpoint: "5min", "20min", "1h", "3h", "6h", "24h", "7d"
    Returns tokens where:
    - Age >= checkpoint delay
    - This checkpoint hasn't been recorded yet
    """
    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"Invalid checkpoint: {checkpoint}. Valid: {list(CHECKPOINTS.keys())}")

    conn = get_connection()
    cursor = conn.cursor()

    min_age_seconds = CHECKPOINTS[checkpoint]
    cutoff = int(time.time()) - min_age_seconds
    mc_col = f"mc_{checkpoint}"

    cursor.execute(f"""
        SELECT contract_address, symbol, api_mc_usd, wallet_name, first_detected_at,
               ath_market_cap, excluded_from_analysis
        FROM token_snapshots
        WHERE {mc_col} IS NULL
        AND first_detected_at < ?
    """, (cutoff,))

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def update_token_checkpoint(
    contract_address: str,
    checkpoint: str,
    current_mc: float,
    current_ath: float = None,
    mc_at_call: float = None,
    holders: int = None,
    liquidity_usd: float = None,
    price_usd: float = None,
    txns_buys: int = None,
    txns_sells: int = None
) -> dict:
    """
    Update token with checkpoint data (mc, holders, liquidity, price, buys, sells).
    Calculates multiples at EVERY checkpoint based on current ATH vs initial MC.
    """
    if checkpoint not in CHECKPOINTS:
        raise ValueError(f"Invalid checkpoint: {checkpoint}")

    conn = get_connection()
    cursor = conn.cursor()

    # Column names for this checkpoint
    mc_col = f"mc_{checkpoint}"
    ath_col = f"ath_{checkpoint}"
    holders_col = f"holders_{checkpoint}"
    liquidity_col = f"liquidity_{checkpoint}"
    price_col = f"price_{checkpoint}"
    buys_col = f"buys_{checkpoint}"
    sells_col = f"sells_{checkpoint}"

    # Get initial MC and current reached values
    cursor.execute("""
        SELECT api_mc_usd, ath_market_cap, current_ath_usd, true_multiple,
               reached_x2, reached_x3, reached_x5, reached_x10, reached_x20, reached_x50, reached_x100
        FROM token_snapshots WHERE contract_address = ?
    """, (contract_address,))
    row = cursor.fetchone()

    if not row:
        # Token not found, just return
        conn.close()
        return {"checkpoint": checkpoint, "error": "Token not found"}

    initial_mc = row['api_mc_usd'] or mc_at_call or 0
    ath_at_call = row['ath_market_cap'] or 0
    prev_ath = row['current_ath_usd'] or ath_at_call or 0
    prev_multiple = row['true_multiple'] or 0

    # Calculate best ATH seen so far
    best_ath = max(prev_ath, current_ath or 0, current_mc or 0)

    # Calculate true multiple based on best ATH vs initial MC
    true_multiple = best_ath / initial_mc if initial_mc > 0 else 0

    # Keep the highest multiple seen
    true_multiple = max(true_multiple, prev_multiple)

    # Update reached_x* - once reached, stays reached (use OR logic)
    reached_x2 = 1 if (row['reached_x2'] or true_multiple >= 2) else 0
    reached_x3 = 1 if (row['reached_x3'] or true_multiple >= 3) else 0
    reached_x5 = 1 if (row['reached_x5'] or true_multiple >= 5) else 0
    reached_x10 = 1 if (row['reached_x10'] or true_multiple >= 10) else 0
    reached_x20 = 1 if (row['reached_x20'] or true_multiple >= 20) else 0
    reached_x50 = 1 if (row['reached_x50'] or true_multiple >= 50) else 0
    reached_x100 = 1 if (row['reached_x100'] or true_multiple >= 100) else 0

    # Update database with checkpoint data AND multiple calculations
    cursor.execute(f"""
        UPDATE token_snapshots
        SET {mc_col} = ?,
            {ath_col} = ?,
            {holders_col} = ?,
            {liquidity_col} = ?,
            {price_col} = ?,
            {buys_col} = ?,
            {sells_col} = ?,
            current_ath_usd = ?,
            true_multiple = ?,
            reached_x2 = ?,
            reached_x3 = ?,
            reached_x5 = ?,
            reached_x10 = ?,
            reached_x20 = ?,
            reached_x50 = ?,
            reached_x100 = ?
        WHERE contract_address = ?
    """, (
        current_mc,
        current_ath,
        holders,
        liquidity_usd,
        price_usd,
        txns_buys,
        txns_sells,
        best_ath,
        true_multiple,
        reached_x2,
        reached_x3,
        reached_x5,
        reached_x10,
        reached_x20,
        reached_x50,
        reached_x100,
        contract_address
    ))

    conn.commit()
    conn.close()

    return {
        "checkpoint": checkpoint,
        "current_mc": current_mc,
        "current_ath": current_ath,
        "best_ath": best_ath,
        "holders": holders,
        "liquidity_usd": liquidity_usd,
        "price_usd": price_usd,
        "txns_buys": txns_buys,
        "txns_sells": txns_sells,
        "true_multiple": true_multiple,
        "reached_x2": reached_x2,
        "reached_x3": reached_x3,
        "reached_x5": reached_x5,
        "reached_x10": reached_x10,
        "reached_x20": reached_x20,
        "reached_x50": reached_x50,
        "reached_x100": reached_x100,
    }


def update_wallet_stats():
    """Recalculate wallet statistics from token outcomes (excluding excluded tokens)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            wallet_name,
            wallet_address,
            COUNT(*) as total_signals,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as total_x2
        FROM token_snapshots
        WHERE mc_7d IS NOT NULL
        AND excluded_from_analysis = 0
        GROUP BY wallet_name
    """)

    results = cursor.fetchall()

    for row in results:
        winrate = (row['total_x2'] / row['total_signals'] * 100) if row['total_signals'] > 0 else 0

        cursor.execute("""
            INSERT OR REPLACE INTO wallet_stats
            (wallet_name, wallet_address, total_signals, total_x2, winrate, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            row['wallet_name'],
            row['wallet_address'],
            row['total_signals'],
            row['total_x2'],
            winrate,
            int(time.time())
        ))

    conn.commit()
    conn.close()

    return [dict(row) for row in results]


def get_stats_by_day_of_week() -> list:
    """Get winrate statistics by day of week (included tokens only)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            day_of_week,
            COUNT(*) as total,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as x2_count
        FROM token_snapshots
        WHERE mc_7d IS NOT NULL
        AND excluded_from_analysis = 0
        AND day_of_week IS NOT NULL
        GROUP BY day_of_week
        ORDER BY day_of_week
    """)

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_stats_by_hour_range() -> list:
    """Get winrate statistics by 4-hour UTC ranges (included tokens only)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            (hour_utc / 4) * 4 as hour_range,
            COUNT(*) as total,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as x2_count
        FROM token_snapshots
        WHERE mc_7d IS NOT NULL
        AND excluded_from_analysis = 0
        AND hour_utc IS NOT NULL
        GROUP BY hour_range
        ORDER BY hour_range
    """)

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_stats_by_platform() -> list:
    """Get winrate statistics by platform (included tokens only)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            CASE
                WHEN platform LIKE '%pump.fun%' THEN 'pump.fun'
                WHEN platform LIKE '%pumpswap%' THEN 'pumpswap'
                WHEN platform LIKE '%letsbonk%' OR platform LIKE '%bonk%' THEN 'letsbonk'
                WHEN platform LIKE '%raydium%' THEN 'raydium'
                ELSE COALESCE(platform, 'unknown')
            END as platform_name,
            COUNT(*) as total,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as x2_count
        FROM token_snapshots
        WHERE mc_7d IS NOT NULL
        AND excluded_from_analysis = 0
        GROUP BY platform_name
        ORDER BY total DESC
    """)

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_stats_by_sol_price() -> list:
    """Get winrate statistics by dynamic $5 SOL price ranges (included tokens only)"""
    conn = get_connection()
    cursor = conn.cursor()

    # Dynamic $5 ranges: floor(price / 5) * 5
    cursor.execute("""
        SELECT
            CAST((CAST(sol_price_at_signal AS INTEGER) / 5) * 5 AS INTEGER) as range_start,
            COUNT(*) as total,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as x2_count
        FROM token_snapshots
        WHERE mc_7d IS NOT NULL
        AND excluded_from_analysis = 0
        AND sol_price_at_signal IS NOT NULL
        GROUP BY range_start
        ORDER BY range_start ASC
    """)

    results = []
    for row in cursor.fetchall():
        start = row['range_start']
        end = start + 5
        results.append({
            'sol_range': f'${start}-{end}',
            'range_start': start,
            'total': row['total'],
            'x2_count': row['x2_count']
        })

    conn.close()
    return results


def get_wallet_stats_detailed() -> list:
    """Get detailed wallet statistics with platform info (included tokens only)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            wallet_name,
            COUNT(*) as total,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as x2_count,
            SUM(CASE WHEN reached_x5 = 1 THEN 1 ELSE 0 END) as x5_count,
            SUM(CASE WHEN reached_x10 = 1 THEN 1 ELSE 0 END) as x10_count,
            (SELECT platform FROM token_snapshots t2
             WHERE t2.wallet_name = token_snapshots.wallet_name
             AND t2.mc_7d IS NOT NULL
             GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 1) as dominant_platform
        FROM token_snapshots
        WHERE mc_7d IS NOT NULL
        AND excluded_from_analysis = 0
        AND wallet_name IS NOT NULL
        GROUP BY wallet_name
        HAVING total >= 3
        ORDER BY (x2_count * 100.0 / total) DESC
    """)

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_global_stats() -> dict:
    """Get global statistics (separating included and excluded tokens)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total_tokens,
            SUM(CASE WHEN mc_7d IS NOT NULL THEN 1 ELSE 0 END) as checked_tokens,
            SUM(CASE WHEN excluded_from_analysis = 1 AND mc_7d IS NOT NULL THEN 1 ELSE 0 END) as excluded_tokens,
            SUM(CASE WHEN reached_x2 = 1 AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as total_x2,
            SUM(CASE WHEN reached_x5 = 1 AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as total_x5,
            SUM(CASE WHEN reached_x10 = 1 AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as total_x10,
            SUM(CASE WHEN mc_7d IS NOT NULL AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as included_tokens
        FROM token_snapshots
    """)

    result = cursor.fetchone()
    conn.close()

    return dict(result) if result else {}


def get_wallet_leaderboard() -> list:
    """Get wallet leaderboard sorted by winrate"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT wallet_name, total_signals, total_x2, winrate
        FROM wallet_stats
        WHERE total_signals >= 3
        ORDER BY winrate DESC
    """)

    results = cursor.fetchall()
    conn.close()

    return [dict(row) for row in results]
