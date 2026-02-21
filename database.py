"""
SQLite database operations for token snapshots
"""
import sqlite3
import os
import time
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "snapshots.db")


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

            -- Résultat J+7
            checked_at_7d             INTEGER,
            market_cap_7d             REAL,
            max_multiple              REAL,
            reached_x2                INTEGER,
            reached_x5                INTEGER,
            reached_x10               INTEGER
        )
    """)

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
    """Get tokens that haven't been checked and are older than min_age_seconds"""
    conn = get_connection()
    cursor = conn.cursor()

    cutoff = int(time.time()) - min_age_seconds

    cursor.execute("""
        SELECT contract_address, symbol, api_mc_usd, wallet_name, first_detected_at
        FROM token_snapshots
        WHERE checked_at_7d IS NULL
        AND first_detected_at < ?
    """, (cutoff,))

    results = cursor.fetchall()
    conn.close()
    return [dict(row) for row in results]


def update_token_outcome(contract_address: str, market_cap_7d: float, initial_mc: float):
    """Update token with 7-day outcome"""
    conn = get_connection()
    cursor = conn.cursor()

    max_multiple = market_cap_7d / initial_mc if initial_mc and initial_mc > 0 else 0

    cursor.execute("""
        UPDATE token_snapshots
        SET checked_at_7d = ?,
            market_cap_7d = ?,
            max_multiple = ?,
            reached_x2 = ?,
            reached_x5 = ?,
            reached_x10 = ?
        WHERE contract_address = ?
    """, (
        int(time.time()),
        market_cap_7d,
        max_multiple,
        1 if max_multiple >= 2 else 0,
        1 if max_multiple >= 5 else 0,
        1 if max_multiple >= 10 else 0,
        contract_address
    ))

    conn.commit()
    conn.close()


def update_wallet_stats():
    """Recalculate wallet statistics from token outcomes"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            wallet_name,
            wallet_address,
            COUNT(*) as total_signals,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as total_x2
        FROM token_snapshots
        WHERE checked_at_7d IS NOT NULL
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


def get_global_stats() -> dict:
    """Get global statistics"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            COUNT(*) as total_tokens,
            SUM(CASE WHEN checked_at_7d IS NOT NULL THEN 1 ELSE 0 END) as checked_tokens,
            SUM(CASE WHEN reached_x2 = 1 THEN 1 ELSE 0 END) as total_x2,
            SUM(CASE WHEN reached_x5 = 1 THEN 1 ELSE 0 END) as total_x5,
            SUM(CASE WHEN reached_x10 = 1 THEN 1 ELSE 0 END) as total_x10
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
