import aiosqlite
import os
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "duck_exchange.db"))

STARTING_CASH = 10_000.0


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                base_price REAL NOT NULL DEFAULT 0.0,
                min_change REAL NOT NULL DEFAULT 0.0,
                max_change REAL NOT NULL DEFAULT 300.0,
                fluctuation_minutes REAL NOT NULL DEFAULT 1.0,
                last_fluctuated TEXT DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                cash REAL NOT NULL DEFAULT 10000.0,
                bank REAL NOT NULL DEFAULT 0.0,
                last_claim TEXT DEFAULT NULL,
                last_steal TEXT DEFAULT NULL,
                last_sell TEXT DEFAULT NULL,
                last_buy TEXT DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                user_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                shares INTEGER NOT NULL DEFAULT 0,
                avg_cost REAL NOT NULL DEFAULT 0.0,
                PRIMARY KEY (user_id, ticker),
                FOREIGN KEY (ticker) REFERENCES stocks(ticker)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                price REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                stock INTEGER NOT NULL DEFAULT -1,
                role_id TEXT DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS community_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_id TEXT NOT NULL,
                seller_name TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                price REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_name TEXT NOT NULL,
                item_description TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'shop'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # Insert default settings (ignore if already exist)
        defaults = [
            ("work_min", "50"),
            ("work_max", "200"),
            ("crime_min", "100"),
            ("crime_max", "500"),
            ("crime_fail_pct", "30"),
            ("steal_fail_pct", "10"),
            ("steal_success_rate", "30"),
            ("transaction_fee_pct", "2"),
            ("claim_reward", "100"),
            ("claim_cooldown_secs", "60"),
        ]
        for key, val in defaults:
            await db.execute(
                "INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)",
                (key, val),
            )
        # Migrate existing DBs — ignore errors if column already exists
        migrations = [
            "ALTER TABLE users ADD COLUMN bank REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE users ADD COLUMN last_claim TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_steal TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_work TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_crime TEXT DEFAULT NULL",
            "ALTER TABLE stocks ADD COLUMN base_price REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE stocks ADD COLUMN min_change REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE stocks ADD COLUMN max_change REAL NOT NULL DEFAULT 300.0",
            "ALTER TABLE stocks ADD COLUMN fluctuation_minutes INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE stocks ADD COLUMN last_fluctuated TEXT DEFAULT NULL",
            "ALTER TABLE holdings ADD COLUMN avg_cost REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE shop_items ADD COLUMN role_id TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN loan_amount REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE users ADD COLUMN loan_due TEXT DEFAULT NULL",
        ]
        for stmt in migrations:
            try:
                await db.execute(stmt)
            except Exception:
                pass
        # Fix base_price for existing stocks that have 0 base_price (set = current price)
        await db.execute("UPDATE stocks SET base_price = price WHERE base_price = 0.0")
        await db.commit()
        await _seed_default_stocks(db)


# ── Settings ───────────────────────────────────────────────────────────────────

async def get_bot_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row else default


async def set_bot_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()


async def get_all_settings() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value FROM bot_settings ORDER BY key") as cur:
            rows = await cur.fetchall()
        return {r["key"]: r["value"] for r in rows}


# ── Users ──────────────────────────────────────────────────────────────────────

async def ensure_user(user_id: str, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, cash, bank) VALUES (?, ?, 0.0, ?)",
            (user_id, username, STARTING_CASH),
        )
        await db.execute(
            "UPDATE users SET username = ? WHERE user_id = ?",
            (username, user_id),
        )
        await db.commit()


async def get_user(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone()


# ── Stocks ─────────────────────────────────────────────────────────────────────

async def get_all_stocks():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM stocks ORDER BY ticker") as cursor:
            return await cursor.fetchall()


async def get_stock(ticker: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM stocks WHERE ticker = ?", (ticker.upper(),)
        ) as cursor:
            return await cursor.fetchone()


async def create_stock(
    ticker: str,
    name: str,
    price: float,
    min_change: float = 0.0,
    max_change: float = 300.0,
    fluctuation_minutes: float = 1.0,
):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """INSERT INTO stocks
                   (ticker, name, price, base_price, min_change, max_change, fluctuation_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ticker.upper(), name, price, price, min_change, max_change, fluctuation_minutes),
            )
            await db.execute(
                "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
                (ticker.upper(), price),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


async def edit_stock(ticker: str, **kwargs) -> bool:
    """Edit stock fields: name, min_change, max_change, fluctuation_minutes."""
    allowed = {"name", "min_change", "max_change", "fluctuation_minutes"}
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT ticker FROM stocks WHERE ticker = ?", (ticker.upper(),)) as cur:
            if not await cur.fetchone():
                return False
        cols = ", ".join(f"{k} = ?" for k in sets)
        await db.execute(
            f"UPDATE stocks SET {cols} WHERE ticker = ?",
            (*sets.values(), ticker.upper()),
        )
        await db.commit()
    return True


async def set_stock_price(ticker: str, new_price: float):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE stocks SET price = ? WHERE ticker = ?",
            (new_price, ticker.upper()),
        )
        await db.execute(
            "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
            (ticker.upper(), new_price),
        )
        await db.commit()


async def get_price_history(ticker: str, limit: int = 20):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT price, recorded_at
            FROM price_history
            WHERE ticker = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (ticker.upper(), limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return list(reversed(rows))


async def get_holding(user_id: str, ticker: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker.upper()),
        ) as cursor:
            return await cursor.fetchone()


async def get_user_holdings(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT h.ticker, h.shares, h.avg_cost, s.price, s.name
            FROM holdings h
            JOIN stocks s ON h.ticker = s.ticker
            WHERE h.user_id = ? AND h.shares > 0
            ORDER BY h.ticker
            """,
            (user_id,),
        ) as cursor:
            return await cursor.fetchall()


PRICE_IMPACT_BUY  = 15.0   # price increase per share bought
PRICE_IMPACT_SELL = 20.0   # price decrease per share sold


async def buy_stock(user_id: str, ticker: str, shares: int, price: float):
    """Returns 'insufficient_funds', 'too_many_shares', 'user_not_found', or (new_price, fee)."""
    fee_pct = float(await get_bot_setting("transaction_fee_pct", "2")) / 100.0
    base_cost = shares * price
    fee = round(base_cost * fee_pct, 2)
    total_cost = round(base_cost + fee, 2)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT COALESCE(SUM(shares), 0) AS total_shares FROM holdings WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            total = await cursor.fetchone()

        if total["total_shares"] + shares > 30:
            return "too_many_shares"

    price_delta = shares * PRICE_IMPACT_BUY
    new_price = round(price + price_delta, 2)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT cash FROM users WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return "user_not_found"
            if row["cash"] < total_cost:
                return "insufficient_funds"

        # Update avg_cost: weighted average
        async with db.execute(
            "SELECT shares, avg_cost FROM holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker.upper()),
        ) as cursor:
            existing = await cursor.fetchone()

        if existing and existing["shares"] > 0:
            old_shares = existing["shares"]
            old_avg = existing["avg_cost"]
            new_avg = round((old_shares * old_avg + shares * price) / (old_shares + shares), 4)
        else:
            new_avg = round(price, 4)

        await db.execute(
            "UPDATE users SET cash = cash - ? WHERE user_id = ?",
            (total_cost, user_id),
        )
        await db.execute(
            """
            INSERT INTO holdings (user_id, ticker, shares, avg_cost)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, ticker) DO UPDATE SET
                shares = shares + ?,
                avg_cost = ?
            """,
            (user_id, ticker.upper(), shares, new_avg, shares, new_avg),
        )
        await db.execute(
            "UPDATE stocks SET price = ? WHERE ticker = ?",
            (new_price, ticker.upper()),
        )
        await db.execute(
            "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
            (ticker.upper(), new_price),
        )
        await db.execute(
            "UPDATE users SET last_buy = ? WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        await db.commit()

    return new_price, fee


async def sell_stock(user_id: str, ticker: str, shares: int, price: float):
    """Returns 'insufficient_shares', 'cooldown', or (new_price, net_proceeds, fee)."""
    from datetime import datetime, timedelta, timezone

    fee_pct = float(await get_bot_setting("transaction_fee_pct", "2")) / 100.0
    gross_proceeds = shares * price
    fee = round(gross_proceeds * fee_pct, 2)
    net_proceeds = round(gross_proceeds - fee, 2)

    price_delta = shares * PRICE_IMPACT_SELL
    raw_new_price = max(round(price - price_delta, 2), 0.01)

    # Enforce price floor at 5% of base_price
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT base_price FROM stocks WHERE ticker = ?", (ticker.upper(),)) as cur:
            stock_row = await cur.fetchone()
        if stock_row and stock_row["base_price"] > 0:
            floor = round(stock_row["base_price"] * 0.05, 2)
            new_price = max(raw_new_price, floor, 0.01)
        else:
            new_price = max(raw_new_price, 0.01)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT shares FROM holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker.upper()),
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row["shares"] < shares:
                return "insufficient_shares"

        async with db.execute(
            "SELECT last_buy, last_sell FROM users WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            user = await cursor.fetchone()

        if user and user["last_buy"]:
            last_buy = datetime.fromisoformat(user["last_buy"])
            if datetime.now(timezone.utc) - last_buy < timedelta(seconds=120):
                return "cooldown"

        await db.execute(
            "UPDATE users SET last_sell = ? WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        await db.execute(
            "UPDATE users SET cash = cash + ? WHERE user_id = ?",
            (net_proceeds, user_id),
        )
        await db.execute(
            "UPDATE holdings SET shares = shares - ? WHERE user_id = ? AND ticker = ?",
            (shares, user_id, ticker.upper()),
        )
        await db.execute(
            "UPDATE stocks SET price = ? WHERE ticker = ?",
            (new_price, ticker.upper()),
        )
        await db.execute(
            "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
            (ticker.upper(), new_price),
        )
        await db.commit()

    return new_price, net_proceeds, fee


async def get_leaderboard(limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT
                u.user_id,
                u.username,
                u.cash,
                u.bank,
                COALESCE(SUM(h.shares * s.price), 0) AS holdings_value
            FROM users u
            LEFT JOIN holdings h ON u.user_id = h.user_id AND h.shares > 0
            LEFT JOIN stocks s ON h.ticker = s.ticker
            GROUP BY u.user_id
            ORDER BY (u.cash + u.bank + COALESCE(SUM(h.shares * s.price), 0)) DESC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            return await cursor.fetchall()


# ── Bank / Wallet ──────────────────────────────────────────────────────────────

async def deposit(user_id: str, amount: float) -> str:
    """Move `amount` from wallet (cash) to bank. Returns 'ok' or 'insufficient_funds'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row or row["cash"] < amount:
            return "insufficient_funds"
        await db.execute(
            "UPDATE users SET cash = cash - ?, bank = bank + ? WHERE user_id = ?",
            (amount, amount, user_id),
        )
        await db.commit()
    return "ok"


async def withdraw(user_id: str, amount: float) -> str:
    """Move `amount` from bank to wallet (cash). Returns 'ok' or 'insufficient_funds'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT bank FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row or row["bank"] < amount:
            return "insufficient_funds"
        await db.execute(
            "UPDATE users SET bank = bank - ?, cash = cash + ? WHERE user_id = ?",
            (amount, amount, user_id),
        )
        await db.commit()
    return "ok"


async def steal_wallet(thief_id: str, target_id: str) -> dict:
    """Attempt theft with configurable success rate and penalties."""
    import random as _rng
    from datetime import datetime, timedelta, timezone

    success_rate = float(await get_bot_setting("steal_success_rate", "30")) / 100.0
    fail_pct = float(await get_bot_setting("steal_fail_pct", "10")) / 100.0

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT cash, bank, last_steal FROM users WHERE user_id = ?", (thief_id,)
        ) as cur:
            thief = await cur.fetchone()
        async with db.execute(
            "SELECT cash FROM users WHERE user_id = ?", (target_id,)
        ) as cur:
            target = await cur.fetchone()

        if not thief or not target:
            return {"error": "user_not_found"}

        if thief["last_steal"]:
            last = datetime.fromisoformat(thief["last_steal"])
            if datetime.now(timezone.utc) - last < timedelta(minutes=5):
                remaining = timedelta(minutes=5) - (datetime.now(timezone.utc) - last)
                return {"error": "cooldown", "seconds": int(remaining.total_seconds())}

        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE users SET last_steal = ? WHERE user_id = ?", (now_iso, thief_id)
        )

        if _rng.random() < success_rate:
            stolen = target["cash"]
            await db.execute("UPDATE users SET cash = 0 WHERE user_id = ?", (target_id,))
            await db.execute(
                "UPDATE users SET cash = cash + ? WHERE user_id = ?", (stolen, thief_id)
            )
            await db.commit()
            async with db.execute("SELECT cash, bank FROM users WHERE user_id = ?", (thief_id,)) as cur:
                new_thief = await cur.fetchone()
            return {
                "success": True, "stolen": stolen,
                "thief_new_cash": new_thief["cash"], "thief_new_bank": new_thief["bank"],
                "target_new_cash": 0.0,
            }
        else:
            total = thief["cash"] + thief["bank"]
            penalty = round(total * fail_pct, 2)
            await db.execute(
                "UPDATE users SET cash = cash - ? WHERE user_id = ?",
                (penalty, thief_id),
            )
            await db.commit()
            async with db.execute("SELECT cash, bank FROM users WHERE user_id = ?", (thief_id,)) as cur:
                new_thief = await cur.fetchone()
            return {
                "success": False, "penalty": penalty,
                "thief_new_cash": new_thief["cash"], "thief_new_bank": new_thief["bank"],
            }


async def admin_give_cash(target_id: str, amount: float) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (target_id,)) as cur:
            if not await cur.fetchone():
                return False
        await db.execute(
            "UPDATE users SET cash = cash + ? WHERE user_id = ?", (amount, target_id)
        )
        await db.commit()
    return True

async def admin_give_bank(target_id: str, amount: float) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (target_id,)) as cur:
            if not await cur.fetchone():
                return False
        await db.execute(
            "UPDATE users SET bank = bank + ? WHERE user_id = ?", (amount, target_id)
        )
        await db.commit()
        return True



async def admin_remove_cash(target_id: str, amount: float) -> str:
    """Returns 'ok', 'user_not_found', or 'insufficient_funds'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (target_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return "user_not_found"
        if row["cash"] < amount:
            return "insufficient_funds"
        await db.execute(
            "UPDATE users SET cash = cash - ? WHERE user_id = ?", (amount, target_id)
        )
        await db.commit()
    return "ok"


async def transfer_cash(sender_id: str, recipient_id: str, amount: float) -> str:
    """Transfer wallet cash between players. Returns 'ok' or 'insufficient_funds'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (sender_id,)) as cur:
            row = await cur.fetchone()
        if not row or row["cash"] < amount:
            return "insufficient_funds"
        await db.execute(
            "UPDATE users SET cash = cash - ? WHERE user_id = ?", (amount, sender_id)
        )
        await db.execute(
            "UPDATE users SET cash = cash + ? WHERE user_id = ?", (amount, recipient_id)
        )
        await db.commit()
    return "ok"


async def claim_daily(user_id: str) -> dict:
    """Give reward if cooldown has passed. Reward and cooldown read from bot_settings."""
    from datetime import datetime, timedelta, timezone
    reward = float(await get_bot_setting("claim_reward", "100"))
    cooldown_secs = int(float(await get_bot_setting("claim_cooldown_secs", "60")))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT last_claim FROM users WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return {"error": "user_not_found"}
        if row["last_claim"]:
            last = datetime.fromisoformat(row["last_claim"])
            diff = datetime.now(timezone.utc) - last
            if diff < timedelta(seconds=cooldown_secs):
                remaining = timedelta(seconds=cooldown_secs) - diff
                return {"ok": False, "seconds_left": int(remaining.total_seconds()), "cooldown_secs": cooldown_secs}
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE users SET cash = cash + ?, last_claim = ? WHERE user_id = ?",
            (reward, now_iso, user_id),
        )
        await db.commit()
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
            updated = await cur.fetchone()
        return {"ok": True, "new_cash": updated["cash"], "reward": reward, "cooldown_secs": cooldown_secs}


async def delete_stock(ticker: str) -> bool:
    ticker = ticker.upper()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT ticker FROM stocks WHERE ticker = ?", (ticker,)) as cursor:
            if not await cursor.fetchone():
                return False
        await db.execute("DELETE FROM holdings WHERE ticker = ?", (ticker,))
        await db.execute("DELETE FROM price_history WHERE ticker = ?", (ticker,))
        await db.execute("DELETE FROM stocks WHERE ticker = ?", (ticker,))
        await db.commit()
        return True


async def fluctuate_all_stocks() -> list[dict]:
    """Roll price changes for each stock, respecting per-stock intervals."""
    import random as _r
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            "SELECT ticker, price, base_price, min_change, max_change, fluctuation_minutes, last_fluctuated FROM stocks"
        ) as cur:
            stocks = await cur.fetchall()

        results = []
        for stock in stocks:
            # Check per-stock interval
            interval = stock["fluctuation_minutes"]
            if stock["last_fluctuated"]:
                last_fluct = datetime.fromisoformat(stock["last_fluctuated"])
                if now - last_fluct < timedelta(minutes=interval):
                    continue  # Not time yet for this stock

            old_price = stock["price"]
            min_c = stock["min_change"]
            max_c = stock["max_change"]
            base_p = stock["base_price"] if stock["base_price"] > 0 else old_price
            floor_price = max(round(base_p * 0.05, 2), 0.01)

            magnitude = max_c - min_c
            if magnitude <= 0:
                change = 0.0
            else:
                category = _r.choices(
                    ["zero", "small", "medium", "large", "xlarge"],
                    weights=[50, 20, 15, 10, 5],
                )[0]
                if category == "zero":
                    change = 0.0
                else:
                    quarter = magnitude / 4
                    band_starts = {
                        "small":  min_c,
                        "medium": min_c + quarter,
                        "large":  min_c + 2 * quarter,
                        "xlarge": min_c + 3 * quarter,
                    }
                    lo = band_starts[category]
                    hi = lo + quarter
                    direction = _r.choices([1, -1], weights=[60, 40])[0]
                    change = round(_r.uniform(lo, hi) * direction, 2)

            # ── Penny-stock skyrocket ─────────────────────────────────────
            # Stocks under $500: 12% chance to rocket up 40–150% this tick
            if old_price < 500 and change != 0 and _r.random() < 0.12:
                change = min(round(old_price * _r.uniform(0.40, 1.50), 2), max_c * 2)

            # ── Market risks ──────────────────────────────────────────────
            # 5% chance of a sudden crash (25-55% drop) — punishes AFK holding
            crashed = False
            if _r.random() < 0.01:
                crash_pct = _r.uniform(0.10, 0.25)
                change = -round(old_price * crash_pct, 2)
                crashed = True





            new_price = max(round(old_price + change, 2), floor_price)
            actual_change = round(new_price - old_price, 2)

            now_iso = now.isoformat()
            await db_conn.execute(
                "UPDATE stocks SET price = ?, last_fluctuated = ? WHERE ticker = ?",
                (new_price, now_iso, stock["ticker"]),
            )
            await db_conn.execute(
                "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
                (stock["ticker"], new_price),
            )
            results.append({
                "ticker": stock["ticker"],
                "old": old_price,
                "new": new_price,
                "change": actual_change,
                "crashed": crashed,
            })
        await db_conn.commit()
    return results


async def get_recent_price_changes(ticker: str, count: int = 5) -> list[float]:
    """Get the last N price deltas for trend analysis."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT price FROM price_history WHERE ticker = ? ORDER BY id DESC LIMIT ?",
            (ticker.upper(), count + 1),
        ) as cur:
            rows = await cur.fetchall()
    prices = [r["price"] for r in reversed(rows)]
    if len(prices) < 2:
        return []
    return [round(prices[i] - prices[i - 1], 2) for i in range(1, len(prices))]


async def get_market_summary() -> list[dict]:
    """Return all stocks with their price change over recent history."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT ticker, price, base_price FROM stocks") as cur:
            stocks = await cur.fetchall()

        results = []
        for stock in stocks:
            async with db.execute(
                """SELECT price FROM price_history WHERE ticker = ?
                   AND recorded_at >= datetime('now', '-24 hours')
                   ORDER BY id ASC LIMIT 1""",
                (stock["ticker"],),
            ) as cur:
                oldest = await cur.fetchone()

            start_price = oldest["price"] if oldest else stock["price"]
            change = round(stock["price"] - start_price, 2)
            pct = round(change / start_price * 100, 1) if start_price != 0 else 0.0
            results.append({
                "ticker": stock["ticker"],
                "price": stock["price"],
                "change": change,
                "pct": pct,
            })
        return sorted(results, key=lambda x: abs(x["pct"]), reverse=True)


async def get_owners_of_stock(ticker: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, shares FROM holdings WHERE ticker = ? AND shares > 0",
            (ticker.upper(),),
        ) as cursor:
            return await cursor.fetchall()


# ── Admin Shop ─────────────────────────────────────────────────────────────────

async def create_shop_item(
    name: str,
    description: str,
    price: float,
    stock: int = -1,
    role_id: str = None,
) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO shop_items (name, description, price, stock, role_id) VALUES (?, ?, ?, ?, ?)",
            (name, description, price, stock, role_id),
        )
        await db.commit()
        return cur.lastrowid


async def get_shop_items():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shop_items ORDER BY price ASC") as cur:
            return await cur.fetchall()


async def get_shop_item(item_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shop_items WHERE id = ?", (item_id,)) as cur:
            return await cur.fetchone()


async def buy_shop_item(user_id: str, item_id: int):
    """Returns 'not_found', 'out_of_stock', 'insufficient_funds', or dict with item info."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM shop_items WHERE id = ?", (item_id,)) as cur:
            item = await cur.fetchone()
        if not item:
            return "not_found"
        if item["stock"] == 0:
            return "out_of_stock"
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
            user = await cur.fetchone()
        if not user or user["cash"] < item["price"]:
            return "insufficient_funds"

        await db.execute("UPDATE users SET cash = cash - ? WHERE user_id = ?", (item["price"], user_id))
        if item["stock"] > 0:
            await db.execute("UPDATE shop_items SET stock = stock - 1 WHERE id = ?", (item_id,))

        # Only add to inventory if this is NOT a role reward
        if not item["role_id"]:
            await db.execute(
                "INSERT INTO user_items (user_id, item_name, item_description, source) VALUES (?, ?, ?, 'shop')",
                (user_id, item["name"], item["description"]),
            )
        await db.commit()

    return {"name": item["name"], "description": item["description"], "role_id": item["role_id"]}


async def delete_shop_item(item_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM shop_items WHERE id = ?", (item_id,)) as cur:
            if not await cur.fetchone():
                return False
        await db.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))
        await db.commit()
    return True


async def edit_shop_item(item_id: int, **kwargs) -> bool:
    allowed = {"name", "description", "price", "stock", "role_id"}
    sets = {k: v for k, v in kwargs.items() if k in allowed}
    if not sets:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM shop_items WHERE id = ?", (item_id,)) as cur:
            if not await cur.fetchone():
                return False
        cols = ", ".join(f"{k} = ?" for k in sets)
        await db.execute(f"UPDATE shop_items SET {cols} WHERE id = ?", (*sets.values(), item_id))
        await db.commit()
    return True


# ── Community Market ───────────────────────────────────────────────────────────

async def create_listing(seller_id: str, seller_name: str, name: str, description: str, price: float) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO community_listings (seller_id, seller_name, name, description, price) VALUES (?, ?, ?, ?, ?)",
            (seller_id, seller_name, name, description, price),
        )
        await db.commit()
        return cur.lastrowid


async def get_listings():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM community_listings ORDER BY created_at DESC") as cur:
            return await cur.fetchall()


async def buy_listing(buyer_id: str, listing_id: int) -> str:
    """Returns 'not_found', 'own_listing', 'insufficient_funds', or 'ok'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM community_listings WHERE id = ?", (listing_id,)) as cur:
            listing = await cur.fetchone()
        if not listing:
            return "not_found"
        if listing["seller_id"] == buyer_id:
            return "own_listing"
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (buyer_id,)) as cur:
            buyer = await cur.fetchone()
        if not buyer or buyer["cash"] < listing["price"]:
            return "insufficient_funds"
        await db.execute("UPDATE users SET cash = cash - ? WHERE user_id = ?", (listing["price"], buyer_id))
        await db.execute("UPDATE users SET cash = cash + ? WHERE user_id = ?", (listing["price"], listing["seller_id"]))
        await db.execute(
            "INSERT INTO user_items (user_id, item_name, item_description, source) VALUES (?, ?, ?, 'market')",
            (buyer_id, listing["name"], listing["description"]),
        )
        await db.execute("DELETE FROM community_listings WHERE id = ?", (listing_id,))
        await db.commit()
    return "ok"


async def delist_item(seller_id: str, listing_id: int) -> str:
    """Returns 'not_found', 'not_yours', or 'ok'. Also returns item to inventory."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM community_listings WHERE id = ?", (listing_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return "not_found"
        if row["seller_id"] != seller_id:
            return "not_yours"
        # Return item to seller's inventory
        await db.execute(
            "INSERT INTO user_items (user_id, item_name, item_description, source) VALUES (?, ?, ?, 'market')",
            (seller_id, row["name"], row["description"]),
        )
        await db.execute("DELETE FROM community_listings WHERE id = ?", (listing_id,))
        await db.commit()
    return "ok"


async def get_user_items(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM user_items WHERE user_id = ? ORDER BY id DESC", (user_id,)
        ) as cur:
            return await cur.fetchall()


async def remove_user_item(user_id: str, item_name: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id FROM user_items WHERE user_id = ? AND item_name = ? LIMIT 1",
            (user_id, item_name),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return False
        await db.execute("DELETE FROM user_items WHERE id = ?", (row["id"],))
        await db.commit()
    return True


# ── Work & Crime ───────────────────────────────────────────────────────────────

async def do_work(user_id: str) -> dict:
    from datetime import datetime, timedelta, timezone
    import random as _r

    work_min = float(await get_bot_setting("work_min", "50"))
    work_max = float(await get_bot_setting("work_max", "200"))

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cash, last_work FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return {"error": "user_not_found"}
        if row["last_work"]:
            last = datetime.fromisoformat(row["last_work"])
            diff = datetime.now(timezone.utc) - last
            if diff < timedelta(minutes=3):
                remaining = timedelta(minutes=3) - diff
                return {"ok": False, "seconds_left": int(remaining.total_seconds())}
        earned = round(_r.uniform(work_min, work_max), 2)
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE users SET cash = cash + ?, last_work = ? WHERE user_id = ?",
            (earned, now_iso, user_id),
        )
        await db.commit()
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
            updated = await cur.fetchone()
        return {"ok": True, "earned": earned, "new_cash": updated["cash"]}


async def do_crime(user_id: str) -> dict:
    from datetime import datetime, timedelta, timezone
    import random as _r

    crime_min = float(await get_bot_setting("crime_min", "100"))
    crime_max = float(await get_bot_setting("crime_max", "500"))
    crime_fail_pct = float(await get_bot_setting("crime_fail_pct", "30")) / 100.0

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT cash, bank, last_crime FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return {"error": "user_not_found"}
        if row["last_crime"]:
            last = datetime.fromisoformat(row["last_crime"])
            diff = datetime.now(timezone.utc) - last
            if diff < timedelta(minutes=5):
                remaining = timedelta(minutes=5) - diff
                return {"ok": False, "seconds_left": int(remaining.total_seconds())}
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute("UPDATE users SET last_crime = ? WHERE user_id = ?", (now_iso, user_id))
        if _r.random() < 0.50:
            earned = round(_r.uniform(crime_min, crime_max), 2)
            await db.execute("UPDATE users SET cash = cash + ? WHERE user_id = ?", (earned, user_id))
            await db.commit()
            async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
                updated = await cur.fetchone()
            return {"ok": True, "success": True, "earned": earned, "new_cash": updated["cash"]}
        else:
            total = row["cash"] + row["bank"]
            penalty = round(total * crime_fail_pct, 2)
            wallet_deduct = min(penalty, row["cash"])
            bank_deduct = penalty - wallet_deduct
            await db.execute(
                "UPDATE users SET cash = cash - ?, bank = bank - ? WHERE user_id = ?",
                (wallet_deduct, bank_deduct, user_id),
            )
            await db.commit()
            async with db.execute("SELECT cash, bank FROM users WHERE user_id = ?", (user_id,)) as cur:
                updated = await cur.fetchone()
            return {
                "ok": True, "success": False, "penalty": penalty,
                "new_cash": updated["cash"], "new_bank": updated["bank"],
            }

async def global_dep(amount: float) -> int:
    """Add *amount* to every stock's current price (negative = decrease). Returns number of stocks updated."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stocks SET price = MAX(0.01, price + ?)", (amount,))
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM stocks") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0



  # ── Bank interest (called every 2 min by bot task) ─────────────────────────

  async def pay_bank_interest() -> int:
      """Add 1% to every user's bank balance. Returns number of users updated."""
      async with aiosqlite.connect(DB_PATH) as db:
          await db.execute("UPDATE users SET bank = ROUND(bank * 1.01, 2) WHERE bank > 0.0")
          await db.commit()
          async with db.execute("SELECT changes()") as cur:
              row = await cur.fetchone()
              return row[0] if row else 0


  # ── Stock dividends (called every 30 min by bot task) ─────────────────────

  async def pay_dividends() -> int:
      """Pay each holding 0.10% of share value as cash. Returns payments made."""
      async with aiosqlite.connect(DB_PATH) as db:
          db.row_factory = aiosqlite.Row
          async with db.execute("""
              SELECT h.user_id, h.shares, s.price
              FROM holdings h JOIN stocks s ON h.ticker = s.ticker
              WHERE h.shares > 0
          """) as cur:
              rows = await cur.fetchall()
          count = 0
          for row in rows:
              dividend = round(row["shares"] * row["price"] * 0.001, 2)
              if dividend > 0:
                  await db.execute(
                      "UPDATE users SET cash = cash + ? WHERE user_id = ?",
                      (dividend, row["user_id"]),
                  )
                  count += 1
          await db.commit()
          return count


  # ── Loan system ────────────────────────────────────────────────────────────

  async def take_loan(user_id: str, amount: float) -> dict:
      """Borrow up to bank balance. Owe 125% back. Due in 24h."""
      from datetime import datetime, timedelta, timezone
      async with aiosqlite.connect(DB_PATH) as db:
          db.row_factory = aiosqlite.Row
          async with db.execute("SELECT cash, bank, loan_amount FROM users WHERE user_id = ?", (user_id,)) as cur:
              user = await cur.fetchone()
          if not user:
              return {"error": "user_not_found"}
          if user["loan_amount"] > 0:
              return {"error": "has_loan", "owed": user["loan_amount"]}
          if amount <= 0:
              return {"error": "invalid_amount"}
          if amount > user["bank"]:
              return {"error": "exceeds_limit", "max": user["bank"]}
          owed = round(amount * 1.25, 2)
          due = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
          await db.execute(
              "UPDATE users SET cash = cash + ?, loan_amount = ?, loan_due = ? WHERE user_id = ?",
              (amount, owed, due, user_id),
          )
          await db.commit()
          async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
              updated = await cur.fetchone()
          return {"ok": True, "borrowed": amount, "owed": owed, "due": due, "new_cash": updated["cash"]}


  async def repay_loan(user_id: str, amount: float) -> dict:
      """Repay part or all of a loan from wallet."""
      async with aiosqlite.connect(DB_PATH) as db:
          db.row_factory = aiosqlite.Row
          async with db.execute("SELECT cash, loan_amount, loan_due FROM users WHERE user_id = ?", (user_id,)) as cur:
              user = await cur.fetchone()
          if not user:
              return {"error": "user_not_found"}
          if not user["loan_amount"] or user["loan_amount"] <= 0:
              return {"error": "no_loan"}
          pay = min(round(amount, 2), user["loan_amount"])
          if user["cash"] < pay:
              return {"error": "insufficient_funds", "has": user["cash"]}
          new_loan = round(user["loan_amount"] - pay, 2)
          await db.execute(
              "UPDATE users SET cash = cash - ?, loan_amount = ?, loan_due = ? WHERE user_id = ?",
              (pay, new_loan, None if new_loan <= 0 else user["loan_due"], user_id),
          )
          await db.commit()
          async with db.execute("SELECT cash, loan_amount FROM users WHERE user_id = ?", (user_id,)) as cur:
              updated = await cur.fetchone()
          return {"ok": True, "paid": pay, "remaining": updated["loan_amount"], "new_cash": updated["cash"]}


  # ── Default stock seeding ──────────────────────────────────────────────────

  async def _seed_default_stocks(db) -> bool:
      """Insert default stocks if the stocks table is empty. Returns True if seeded."""
      async with db.execute("SELECT COUNT(*) FROM stocks") as cur:
          count = (await cur.fetchone())[0]
      if count > 0:
          return False
      defaults = [
        ("QUAK", "Quackington Holdings Ltd.",        15000.0, 0.0, 1500.0, 1.5),
        ("BRDD", "Breadsworth & Associates",          16500.0, 0.0, 1500.0, 1.5),
        ("WDPL", "Waddle & Paddle Financial Corp",   18000.0, 0.0, 2000.0, 2.0),
        ("SQWK", "Squawksworth Ventures Inc.",        15500.0, 0.0, 1500.0, 1.0),
        ("DKPT", "Duckpoint Capital Partners",        20000.0, 0.0, 2000.0, 2.0),
        ("MLFT", "Molted Feather Industries Inc.",    16000.0, 0.0, 1500.0, 2.0),
        ("FWNG", "Fowington Group International",    19500.0, 0.0, 2000.0, 2.0),
        ("PRPT", "Preenington Proprietary Ltd.",      17000.0, 0.0, 1500.0, 1.5),
        ("NSTG", "Nestington Global Securities",     18500.0, 0.0, 2000.0, 1.5),
        ("BLLP", "Billington & Lakesworth Partners",  15000.0, 0.0, 1500.0, 2.0),
      ]
      for ticker, name, price, min_c, max_c, interval in defaults:
          await db.execute(
              """INSERT OR IGNORE INTO stocks (ticker, name, price, base_price, min_change, max_change, fluctuation_minutes)
                 VALUES (?, ?, ?, ?, ?, ?, ?)""",
              (ticker, name, price, price, min_c, max_c, interval),
          )
          await db.execute(
              "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
              (ticker, price),
          )
      await db.commit()
      return True
  