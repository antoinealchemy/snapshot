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
            risk_rugged               INTEGER,
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

            -- /first-buyers/{token} - 1 appel
            early_buyers_count            INTEGER,
            early_buyers_still_holding    INTEGER,
            early_buyers_avg_pnl          REAL,

            -- Prix SOL au moment du signal
            sol_price_at_signal           REAL,

            -- Champs temporels (calculés depuis first_detected_at)
            day_of_week               INTEGER,
            hour_utc                  INTEGER,
            week_number               INTEGER,
            month                     INTEGER,

            -- Exclusion de l'analyse (ath_ratio < 0.5)
            excluded_from_analysis    INTEGER DEFAULT 0,

            -- Vérifications multi-jours
            checked_j1                INTEGER,
            checked_j3                INTEGER,
            checked_j7                INTEGER,

            -- Résultats
            current_ath_usd           REAL,
            true_multiple             REAL,
            detection_type            TEXT,
            reached_x2                INTEGER,
            reached_x5                INTEGER,
            reached_x10               INTEGER
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
        ("checked_j1", "INTEGER"),
        ("checked_j3", "INTEGER"),
        ("checked_j7", "INTEGER"),
        ("current_ath_usd", "REAL"),
        ("true_multiple", "REAL"),
        ("detection_type", "TEXT"),
    ]

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


def get_unchecked_tokens(min_age_seconds: int = 604800) -> list:
    """Get tokens that haven't been checked and are older than min_age_seconds (legacy)"""
    conn = get_connection()
    cursor = conn.cursor()

    cutoff = int(time.time()) - min_age_seconds

    cursor.execute("""
        SELECT contract_address, symbol, api_mc_usd, wallet_name, first_detected_at,
               ath_market_cap, excluded_from_analysis
        FROM token_snapshots
        WHERE checked_j7 IS NULL
        AND first_detected_at < ?
    """, (cutoff,))

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_tokens_for_check(check_day: int) -> list:
    """
    Get tokens ready for a specific day check.
    check_day: 1, 3, or 7
    Returns tokens where:
    - Age >= check_day days
    - This check hasn't been done yet
    - Not already ATH_CONFIRMED (for J+1 and J+3)
    """
    conn = get_connection()
    cursor = conn.cursor()

    min_age_seconds = check_day * 24 * 3600
    cutoff = int(time.time()) - min_age_seconds

    check_col = f"checked_j{check_day}"

    if check_day == 7:
        # J+7: check all unchecked
        cursor.execute(f"""
            SELECT contract_address, symbol, api_mc_usd, wallet_name, first_detected_at,
                   ath_market_cap, excluded_from_analysis, detection_type
            FROM token_snapshots
            WHERE {check_col} IS NULL
            AND first_detected_at < ?
        """, (cutoff,))
    else:
        # J+1, J+3: skip if already ATH_CONFIRMED
        cursor.execute(f"""
            SELECT contract_address, symbol, api_mc_usd, wallet_name, first_detected_at,
                   ath_market_cap, excluded_from_analysis, detection_type
            FROM token_snapshots
            WHERE {check_col} IS NULL
            AND first_detected_at < ?
            AND (detection_type IS NULL OR detection_type != 'ATH_CONFIRMED')
        """, (cutoff,))

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def update_token_outcome(contract_address: str, market_cap_7d: float, initial_mc: float):
    """Update token with 7-day outcome (legacy compatibility)"""
    conn = get_connection()
    cursor = conn.cursor()

    max_multiple = market_cap_7d / initial_mc if initial_mc and initial_mc > 0 else 0

    cursor.execute("""
        UPDATE token_snapshots
        SET checked_j7 = ?,
            true_multiple = ?,
            reached_x2 = ?,
            reached_x5 = ?,
            reached_x10 = ?
        WHERE contract_address = ?
    """, (
        int(time.time()),
        max_multiple,
        1 if max_multiple >= 2 else 0,
        1 if max_multiple >= 5 else 0,
        1 if max_multiple >= 10 else 0,
        contract_address
    ))

    conn.commit()
    conn.close()


def update_token_check(
    contract_address: str,
    check_day: int,
    current_mc: float,
    current_ath: float,
    ath_at_call: float,
    mc_at_call: float
) -> dict:
    """
    Update token with check results for a specific day.
    Returns the result dict with detection_type and multiple.
    """
    conn = get_connection()
    cursor = conn.cursor()

    check_col = f"checked_j{check_day}"

    # Determine detection type and multiple
    if current_ath > ath_at_call and ath_at_call > 0:
        # New ATH confirmed
        detection_type = "ATH_CONFIRMED"
        true_multiple = current_ath / mc_at_call if mc_at_call > 0 else 0
    else:
        # Approximation based on current MC
        detection_type = "APPROXIMATION"
        true_multiple = current_mc / mc_at_call if mc_at_call > 0 else 0

    reached_x2 = 1 if true_multiple >= 2 else 0
    reached_x5 = 1 if true_multiple >= 5 else 0
    reached_x10 = 1 if true_multiple >= 10 else 0

    cursor.execute(f"""
        UPDATE token_snapshots
        SET {check_col} = ?,
            current_ath_usd = ?,
            true_multiple = ?,
            detection_type = ?,
            reached_x2 = ?,
            reached_x5 = ?,
            reached_x10 = ?
        WHERE contract_address = ?
    """, (
        int(time.time()),
        current_ath,
        true_multiple,
        detection_type,
        reached_x2,
        reached_x5,
        reached_x10,
        contract_address
    ))

    conn.commit()
    conn.close()

    return {
        "detection_type": detection_type,
        "true_multiple": true_multiple,
        "reached_x2": reached_x2,
        "reached_x5": reached_x5,
        "reached_x10": reached_x10,
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
        WHERE checked_j7 IS NOT NULL
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
        WHERE checked_j7 IS NOT NULL
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
        WHERE checked_j7 IS NOT NULL
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
        WHERE checked_j7 IS NOT NULL
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
        WHERE checked_j7 IS NOT NULL
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
             AND t2.checked_j7 IS NOT NULL
             GROUP BY platform ORDER BY COUNT(*) DESC LIMIT 1) as dominant_platform
        FROM token_snapshots
        WHERE checked_j7 IS NOT NULL
        AND excluded_from_analysis = 0
        AND wallet_name IS NOT NULL
        GROUP BY wallet_name
        HAVING total >= 3
        ORDER BY (x2_count * 100.0 / total) DESC
    """)

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def get_detection_type_stats() -> dict:
    """Get statistics on detection types"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            detection_type,
            COUNT(*) as count
        FROM token_snapshots
        WHERE checked_j7 IS NOT NULL
        AND excluded_from_analysis = 0
        GROUP BY detection_type
    """)

    results = {row['detection_type']: row['count'] for row in cursor.fetchall()}
    conn.close()
    return results


def get_global_stats() -> dict:
    """Get global statistics (separating included and excluded tokens)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total_tokens,
            SUM(CASE WHEN checked_j7 IS NOT NULL THEN 1 ELSE 0 END) as checked_tokens,
            SUM(CASE WHEN excluded_from_analysis = 1 AND checked_j7 IS NOT NULL THEN 1 ELSE 0 END) as excluded_tokens,
            SUM(CASE WHEN reached_x2 = 1 AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as total_x2,
            SUM(CASE WHEN reached_x5 = 1 AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as total_x5,
            SUM(CASE WHEN reached_x10 = 1 AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as total_x10,
            SUM(CASE WHEN checked_j7 IS NOT NULL AND excluded_from_analysis = 0 THEN 1 ELSE 0 END) as included_tokens
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
