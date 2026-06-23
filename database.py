import aiosqlite
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "duck_exchange.db")

STARTING_CASH = 10_000.0


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stocks (
                ticker TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                cash REAL NOT NULL DEFAULT 10000.0,
                bank REAL NOT NULL DEFAULT 0.0,
                last_claim TEXT DEFAULT NULL,
                last_steal TEXT DEFAULT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                user_id TEXT NOT NULL,
                ticker TEXT NOT NULL,
                shares INTEGER NOT NULL DEFAULT 0,
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
        # Migrate existing DBs — ignore errors if column already exists
        for stmt in [
            "ALTER TABLE users ADD COLUMN bank REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE users ADD COLUMN last_claim TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_steal TEXT DEFAULT NULL",
        ]:
            try:
                await db.execute(stmt)
            except Exception:
                pass
        await db.commit()


async def ensure_user(user_id: str, username: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, cash, bank) VALUES (?, ?, ?, 0.0)",
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


async def create_stock(ticker: str, name: str, price: float):
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                "INSERT INTO stocks (ticker, name, price) VALUES (?, ?, ?)",
                (ticker.upper(), name, price),
            )
            await db.execute(
                "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
                (ticker.upper(), price),
            )
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False


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
            SELECT h.ticker, h.shares, s.price, s.name
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
    """Returns 'insufficient_funds', 'user_not_found', or new price (float)."""
    cost = shares * price
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
            if row["cash"] < cost:
                return "insufficient_funds"

        await db.execute(
            "UPDATE users SET cash = cash - ? WHERE user_id = ?",
            (cost, user_id),
        )
        await db.execute(
            """
            INSERT INTO holdings (user_id, ticker, shares)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, ticker) DO UPDATE SET shares = shares + ?
            """,
            (user_id, ticker.upper(), shares, shares),
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
    return new_price


async def sell_stock(user_id: str, ticker: str, shares: int, price: float):
    """Returns 'insufficient_shares' or new price (float)."""
    proceeds = shares * price
    price_delta = shares * PRICE_IMPACT_SELL
    new_price = max(round(price - price_delta, 2), 0.01)

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT shares FROM holdings WHERE user_id = ? AND ticker = ?",
            (user_id, ticker.upper()),
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row["shares"] < shares:
                return "insufficient_shares"

        await db.execute(
            "UPDATE users SET cash = cash + ? WHERE user_id = ?",
            (proceeds, user_id),
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
    return new_price


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
    """
    30% chance: thief steals ALL of target's wallet (cash).
    70% chance: thief loses 10% of total wealth (cash + bank).
    Returns dict with keys: success, stolen, penalty, thief_new_cash, thief_new_bank, target_new_cash
    Also checks/sets last_steal cooldown (5 min).
    """
    import random as _rng
    from datetime import datetime, timedelta, timezone
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

        # Cooldown check (5 minutes)
        if thief["last_steal"]:
            last = datetime.fromisoformat(thief["last_steal"])
            if datetime.now(timezone.utc) - last < timedelta(minutes=5):
                remaining = timedelta(minutes=5) - (datetime.now(timezone.utc) - last)
                return {"error": "cooldown", "seconds": int(remaining.total_seconds())}

        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE users SET last_steal = ? WHERE user_id = ?", (now_iso, thief_id)
        )

        if _rng.random() < 0.30:
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
            penalty = round(total * 0.10, 2)
            # Deduct penalty from wallet first, then bank if needed
            wallet_deduct = min(penalty, thief["cash"])
            bank_deduct = penalty - wallet_deduct
            await db.execute(
                "UPDATE users SET cash = cash - ?, bank = bank - ? WHERE user_id = ?",
                (wallet_deduct, bank_deduct, thief_id),
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


async def claim_daily(user_id: str, reward: float = 100.0) -> dict:
    """Give reward if cooldown (60s) has passed. Returns dict with ok/seconds_left."""
    from datetime import datetime, timedelta, timezone
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
            if diff < timedelta(seconds=60):
                remaining = timedelta(seconds=60) - diff
                return {"ok": False, "seconds_left": int(remaining.total_seconds())}
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE users SET cash = cash + ?, last_claim = ? WHERE user_id = ?",
            (reward, now_iso, user_id),
        )
        await db.commit()
        async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
            updated = await cur.fetchone()
        return {"ok": True, "new_cash": updated["cash"]}


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


async def get_owners_of_stock(ticker: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, shares FROM holdings WHERE ticker = ? AND shares > 0",
            (ticker.upper(),),
        ) as cursor:
            return await cursor.fetchall()
