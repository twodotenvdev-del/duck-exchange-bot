import aiosqlite
import os
from datetime import datetime, timezone

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
                stock INTEGER NOT NULL DEFAULT -1
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
        # Migrate existing DBs — ignore errors if column already exists
        for stmt in [
            "ALTER TABLE users ADD COLUMN bank REAL NOT NULL DEFAULT 0.0",
            "ALTER TABLE users ADD COLUMN last_claim TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_steal TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_work TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN last_crime TEXT DEFAULT NULL",
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
    """Returns 'insufficient_funds', 'too_many_shares', 'user_not_found', or new price (float)."""
    cost = shares * price

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
        await db.execute(
            "UPDATE users SET last_buy = ? WHERE user_id = ?",
            (datetime.now(timezone.utc).isoformat(), user_id),
        )
        await db.commit()
    return new_price

async def sell_stock(user_id: str, ticker: str, shares: int, price: float):
    """Returns 'insufficient_shares', 'cooldown' or new price (float)."""
    from datetime import datetime, timedelta, timezone

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


        async with db.execute(
            "SELECT last_sell FROM users WHERE user_id = ?",
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


async def claim_daily(user_id: str, reward: float = 500.0) -> dict:
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


async def fluctuate_all_stocks() -> list[dict]:
    """Roll an independent random price change for every stock. Returns list of {ticker, old, new, change}."""
    import random as _r

    def roll_change() -> float:
        category = _r.choices(
            ["zero", "small", "medium", "large", "xlarge"],
            weights=[50, 20, 15, 10, 5],
        )[0]
        if category == "zero":
            return 0.0
        magnitude = {
            "small":  _r.uniform(1, 100),
            "medium": _r.uniform(100, 150),
            "large":  _r.uniform(150, 200),
            "xlarge": _r.uniform(200, 300),
        }[category]
        return round(magnitude * _r.choice([1, -1]), 2)

    async with aiosqlite.connect(DB_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute("SELECT ticker, price FROM stocks") as cur:
            stocks = await cur.fetchall()
        results = []
        for stock in stocks:
            old_price = stock["price"]
            change = roll_change()
            new_price = max(0.01, round(old_price + change, 2))
            await db_conn.execute("UPDATE stocks SET price = ? WHERE ticker = ?", (new_price, stock["ticker"]))
            await db_conn.execute(
                "INSERT INTO price_history (ticker, price) VALUES (?, ?)",
                (stock["ticker"], new_price),
            )
            results.append({"ticker": stock["ticker"], "old": old_price, "new": new_price, "change": round(new_price - old_price, 2)})
        await db_conn.commit()
    return results


async def get_owners_of_stock(ticker: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, shares FROM holdings WHERE ticker = ? AND shares > 0",
            (ticker.upper(),),
        ) as cursor:
            return await cursor.fetchall()


# ── Admin Shop ─────────────────────────────────────────────────────────────────

async def create_shop_item(name: str, description: str, price: float, stock: int = -1) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO shop_items (name, description, price, stock) VALUES (?, ?, ?, ?)",
            (name, description, price, stock),
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


async def buy_shop_item(user_id: str, item_id: int) -> str:
    """Returns 'not_found', 'out_of_stock', 'insufficient_funds', or 'ok'."""
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
        await db.execute(
            "INSERT INTO user_items (user_id, item_name, item_description, source) VALUES (?, ?, ?, 'shop')",
            (user_id, item["name"], item["description"]),
        )
        await db.commit()
    return "ok"


async def delete_shop_item(item_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM shop_items WHERE id = ?", (item_id,)) as cur:
            if not await cur.fetchone():
                return False
        await db.execute("DELETE FROM shop_items WHERE id = ?", (item_id,))
        await db.commit()
    return True


async def edit_shop_item(item_id: int, **kwargs) -> bool:
    allowed = {"name", "description", "price", "stock"}
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
    """Returns 'not_found', 'not_yours', or 'ok'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT seller_id FROM community_listings WHERE id = ?", (listing_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return "not_found"
        if row["seller_id"] != seller_id:
            return "not_yours"
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
        db.row_factory = aiosqlite.Row

        async with db.execute(
            """
            SELECT id
            FROM user_items
            WHERE user_id = ? AND item_name = ?
            ORDER BY id ASC
            LIMIT 1
            """,
            (user_id, item_name),
        ) as cur:
            item = await cur.fetchone()

        if not item:
            return False

        await db.execute(
            "DELETE FROM user_items WHERE id = ?",
            (item["id"],)
        )
        await db.commit()
        return True

# ── Work & Crime ───────────────────────────────────────────────────────────────

async def do_work(user_id: str) -> dict:
    from datetime import datetime, timedelta, timezone
    import random as _r
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
        earned = round(_r.uniform(150, 600), 2)
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
            earned = round(_r.uniform(300, 1500), 2)
            await db.execute("UPDATE users SET cash = cash + ? WHERE user_id = ?", (earned, user_id))
            await db.commit()
            async with db.execute("SELECT cash FROM users WHERE user_id = ?", (user_id,)) as cur:
                updated = await cur.fetchone()
            return {"ok": True, "success": True, "earned": earned, "new_cash": updated["cash"]}
        else:
            total = row["cash"] + row["bank"]
            penalty = round(total * 0.30, 2)
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
