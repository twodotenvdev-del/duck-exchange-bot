import os
import io
import random
import asyncio
import threading
import aiosqlite
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord import app_commands
from discord.ext import commands, tasks
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import database as db

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN environment variable is not set.")


# ── Keep-alive HTTP server (prevents Replit from sleeping) ───────────────────

class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Duck Exchange is running!")

    def log_message(self, *args):
        pass  # silence access logs


def _start_keepalive():
    port = int(os.environ.get("PORT", 3001))
    server = HTTPServer(("0.0.0.0", port), _PingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Keep-alive server listening on port {port}")


_start_keepalive()


def fmt_money(amount: float) -> str:
    return f"${amount:,.2f}"


class DuckExchangeBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # Required for prefix commands like ?cs
        super().__init__(command_prefix=["!", "?"], intents=intents)

    async def setup_hook(self):
        await db.init_db()
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Duck Exchange is online as {self.user} (ID: {self.user.id})")
        if not stock_fluctuation.is_running():
            stock_fluctuation.start()
        if not bank_interest.is_running():
            bank_interest.start()
        if not dividend_payout.is_running():
            dividend_payout.start()
        if not holding_tax.is_running():
            holding_tax.start()
        if not loan_enforcer.is_running():
            loan_enforcer.start()


bot = DuckExchangeBot()


# ── Stock price fluctuation task ───────────────────────────────────────────────

@tasks.loop(minutes=1)

async def stock_fluctuation():
    results = await db.fluctuate_all_stocks()
    if results:
        changed = [r for r in results if r["change"] != 0]
        if changed:
            lines = []
            for r in changed:
                if r.get("crashed"):
                    arrow = "💥"
                elif r["change"] > 0:
                    arrow = "📈"
                else:
                    arrow = "📉"
                sign = "+" if r["change"] > 0 else ""
                lines.append(f"{arrow} **{r['ticker']}**  {fmt_money(r['old'])} → {fmt_money(r['new'])}  ({sign}{fmt_money(r['change'])})")
            crashes = sum(1 for r in changed if r.get("crashed"))
            print(f"[Fluctuation] {len(changed)}/{len(results)} stocks moved, {crashes} crash(es)")



# ── Bank interest task (every 2 min) ──────────────────────────────────────

@tasks.loop(minutes=2)
async def bank_interest():
    count = await db.pay_bank_interest()
    if count:
        print(f"[Interest] 1% bank interest paid to {count} users")


# ── Dividend payout task (every 30 min) ───────────────────────────────────

@tasks.loop(minutes=30)
async def dividend_payout():
    count = await db.pay_dividends()
    if count:
        print(f"[Dividends] 0.1% dividend paid across {count} holdings")


# ── Holding tax task (every 5 min) ──────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def holding_tax():
    count = await db.pay_holding_tax()
    if count:
        print(f"[Holding Tax] 1% tax collected from {count} holders")


@tasks.loop(minutes=1)
async def loan_enforcer():
    collected = await db.collect_overdue_loans()
    for entry in collected:
        print(f"[Loan] Auto-collected overdue loan of {fmt_money(entry['amount'])} from {entry['username']}")



def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    return interaction.permissions.administrator


async def ensure(interaction: discord.Interaction):
    await db.ensure_user(str(interaction.user.id), interaction.user.display_name)


async def ensure_prefix(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)


def render_chart_image(prices: list[float], ticker: str, name: str, shareholders: int = 0, base_price: float = 0.0) -> io.BytesIO:
    """Render a candlestick-style chart image and return it as a BytesIO PNG."""
    BG      = "#0d1b2a"
    GRID    = "#1a2e42"
    TEXT    = "#dce8f0"
    GREEN   = "#26a65b"
    RED     = "#e84040"
    NEUTRAL = "#7a8fa0"
    YELLOW  = "#f1c40f"
    GRAY    = "#888888"

    price_range = max(prices) - min(prices) if max(prices) != min(prices) else prices[0] * 0.1

    MAX_SLOTS = 40
    fig, ax = plt.subplots(figsize=(14, 4))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    body_width = 0.95

    for i, price in enumerate(prices):
        if i == 0:
            half = max(price_range * 0.015, price * 0.005)
            body_lo, body_hi = price - half, price + half
            color = NEUTRAL
        else:
            prev = prices[i - 1]
            body_lo = min(prev, price)
            body_hi = max(prev, price)
            if price > prev:
                color = GREEN
            elif price < prev:
                color = RED
            else:
                color = YELLOW  # no change = yellow

        body_h = max(body_hi - body_lo, price_range * 0.004)
        rect = mpatches.FancyBboxPatch(
            (i - body_width / 2, body_lo), body_width, body_h,
            boxstyle="square,pad=0", linewidth=0, facecolor=color, zorder=3,
        )
        ax.add_patch(rect)

    ax.set_xlim(-0.5, MAX_SLOTS - 0.5)

    # Always keep base_price visible in y-axis
    y_min = min(prices) - price_range * 0.05
    y_max = max(prices) + price_range * 0.1
    if base_price > 0:
        y_min = min(y_min, base_price - price_range * 0.02)
        y_max = max(y_max, base_price + price_range * 0.02)
    ax.set_ylim(y_min, y_max)

    # Base price reference line
    if base_price > 0:
        ax.axhline(y=base_price, color=GRAY, linewidth=1.2, linestyle="--", zorder=1, alpha=0.8)
        ax.text(MAX_SLOTS - 0.3, base_price, f" Base {fmt_money(base_price)}",
                color=GRAY, fontsize=7.5, va="center", zorder=4)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.2f}"))
    ax.yaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID)
    ax.tick_params(colors=TEXT, labelsize=8.5)

    overall = prices[-1] - prices[0]
    pct = (overall / prices[0] * 100) if prices[0] != 0 else 0
    arrow = "▲" if overall >= 0 else "▼"
    change_color = GREEN if overall >= 0 else RED
    holder_str = f"   ·   {shareholders:,} holder{'s' if shareholders != 1 else ''}"

    plt.tight_layout(rect=[0, 0, 1, 0.83])

    # Line 1: ticker — full name  (white, top)
    fig.text(
        0.5, 0.935,
        f"{ticker}  —  {name}",
        ha="center", fontsize=12, fontweight="bold", color=TEXT,
    )
    # Line 2: price + change + shareholders  (green/red, below name)
    fig.text(
        0.5, 0.865,
        f"${prices[-1]:,.2f}   {arrow} ${abs(overall):,.2f} ({pct:+.1f}%){holder_str}",
        ha="center", fontsize=9, color=change_color,
    )

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf


@bot.tree.command(name="stocks", description="View all available stocks and their current prices.")
async def stocks_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await db.get_all_stocks()
    if not rows:
        await interaction.followup.send("No stocks have been created yet. An admin can use `/createstock` to add one.")
        return

    supply = await db.get_all_stocks_supply()
    embed = discord.Embed(
        title="🦆 Duck Exchange — Stock Market",
        color=discord.Color.yellow(),
    )
    lines = []
    for row in rows:
        s = supply.get(row["ticker"], {"held": 0, "max": 50})
        remaining = s["max"] - s["held"]
        lines.append(
            f"**{row['ticker']}** — {row['name']} — {fmt_money(row['price'])}"
            f"  `{remaining:,}/{s['max']:,} shares left`"
        )
    embed.description = "\n".join(lines)
    embed.set_footer(text="Use /buy <stock> <amount> to invest!")
    await interaction.followup.send(embed=embed)


# ── /portfolio ────────────────────────────────────────────────────────────────

# ── /stock (single stock detail) ──────────────────────────────────────────────────────

@bot.tree.command(name="stock", description="View detailed info about a specific stock.")
@app_commands.describe(ticker="Stock ticker symbol (e.g. DUCK)")
async def stock_cmd(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.followup.send(f"❌ No stock with ticker **{ticker}** found.", ephemeral=True)
        return

    owners = await db.get_owners_of_stock(ticker)
    changes = await db.get_recent_price_changes(ticker, 5)
    fee_pct = await db.get_bot_setting("transaction_fee_pct", "2")

    if changes:
        net = sum(changes)
        trend = "📈 Trending Up" if net > 0 else ("📉 Trending Down" if net < 0 else "➡️ Flat")
    else:
        trend = "❓ No data yet"

    base_p = stock["base_price"] if stock["base_price"] > 0 else stock["price"]
    vs_base = stock["price"] - base_p
    vs_base_pct = (vs_base / base_p * 100) if base_p != 0 else 0.0
    vs_sign = "+" if vs_base >= 0 else ""
    floor = round(base_p * 0.05, 2)

    if changes:
        change_str = "  ".join(
            ("🟡 +$0.00" if c == 0 else ("🟢 +" + fmt_money(c) if c > 0 else "🔴 " + fmt_money(c)))
            for c in changes
        )
    else:
        change_str = "No history"

    shares_held = await db.get_shares_held(ticker)
    max_s = stock["max_shares"] if stock["max_shares"] else 50
    remaining = max_s - shares_held

    embed = discord.Embed(
        title=f"📊 {ticker} — {stock['name']}",
        color=discord.Color.yellow(),
    )
    embed.add_field(name="💵 Current Price", value=fmt_money(stock["price"]), inline=True)
    embed.add_field(name="🏁 Base Price", value=fmt_money(base_p), inline=True)
    embed.add_field(name="📐 vs Base", value=f"{vs_sign}{fmt_money(vs_base)} ({vs_sign}{vs_base_pct:.1f}%)", inline=True)
    embed.add_field(name="🛡️ Price Floor", value=fmt_money(floor), inline=True)
    embed.add_field(name="👥 Shareholders", value=str(len(owners)), inline=True)
    embed.add_field(name="📉 Trend", value=trend, inline=True)
    embed.add_field(name="📦 Supply", value=f"{remaining:,}/{max_s:,} shares left", inline=True)
    embed.add_field(
        name="⚙️ Volatility",
        value=f"Change range: ${stock['min_change']:,.0f}–${stock['max_change']:,.0f}\nInterval: every **{stock['fluctuation_minutes']}** min",
        inline=False,
    )
    embed.add_field(name="🕐 Last 5 Changes", value=change_str, inline=False)
    embed.set_footer(text=f"Transaction fee: {fee_pct}% on buy & sell  ·  Use /chart for price history")
    await interaction.followup.send(embed=embed)


# ── /portfolio ────────────────────────────────────────────────────────

@bot.tree.command(name="portfolio", description="View your cash, shares, and total net worth.")
async def portfolio_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    await ensure(interaction)

    user = await db.get_user(str(interaction.user.id))
    holdings = await db.get_user_holdings(str(interaction.user.id))

    cash = user["cash"]
    bank = user["bank"]
    holdings_value = sum(h["shares"] * h["price"] for h in holdings)
    net_worth = cash + bank + holdings_value

    embed = discord.Embed(
        title=f"🦆 {interaction.user.display_name}'s Portfolio",
        color=discord.Color.green(),
    )
    embed.add_field(name="👛 Wallet", value=fmt_money(cash), inline=True)
    embed.add_field(name="🏦 Bank", value=fmt_money(bank), inline=True)
    embed.add_field(name="📈 Holdings Value", value=fmt_money(holdings_value), inline=True)
    embed.add_field(name="🏆 Net Worth", value=fmt_money(net_worth), inline=True)

    if holdings:
        lines = []
        for h in holdings:
            current_value = h["shares"] * h["price"]
            cost_basis = h["shares"] * h["avg_cost"]
            pl = current_value - cost_basis
            pl_pct = (pl / cost_basis * 100) if cost_basis > 0 else 0.0
            pl_sign = "+" if pl >= 0 else ""
            pl_emoji = "📈" if pl >= 0 else "📉"
            lines.append(
                f"**{h['ticker']}** ({h['name']}) — {h['shares']:,} shares\n"
                f"  Bought @ {fmt_money(h['avg_cost'])} avg  ·  Now {fmt_money(h['price'])}\n"
                f"  Value: {fmt_money(current_value)}  ·  P&L: {pl_emoji} {pl_sign}{fmt_money(pl)} ({pl_sign}{pl_pct:.1f}%)"
            )
        embed.add_field(name="📊 Shares Owned", value="\n\n".join(lines), inline=False)
    else:
        embed.add_field(name="📊 Shares Owned", value="None — use `/buy` to get started!", inline=False)

    await interaction.followup.send(embed=embed)


# ── /buy ────────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="buy", description="Buy shares of a stock.")
@app_commands.describe(ticker="Stock ticker symbol (e.g. DUCK)", amount="Number of shares to buy")
async def buy_cmd(interaction: discord.Interaction, ticker: str, amount: int):
    await interaction.response.defer()
    await ensure(interaction)
    if amount <= 0:
        await interaction.followup.send("❌ Amount must be a positive number.")
        return
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.followup.send(f"❌ No stock with ticker **{ticker}** found. Use `/stocks` to see available stocks.")
        return
    cost = amount * stock["price"]
    result = await db.buy_stock(str(interaction.user.id), ticker, amount, stock["price"])
    if result == "too_many_shares":
        holdings = await db.get_user_holdings(str(interaction.user.id))
        owned = sum(h["shares"] for h in holdings if h["ticker"] == ticker)
        cap = stock["max_shares"]
        await interaction.followup.send(
            f"❌ **{ticker}** has a total supply of **{cap:,} shares** and they're all accounted for.\n"
            f"You own: **{owned:,}/{cap:,}**  ·  Trying to buy: **{amount:,}**"
        )
        return
    if result == "insufficient_funds":
        fee_pct = await db.get_bot_setting("transaction_fee_pct", "2")
        await interaction.followup.send(
            f"❌ You don't have enough cash.\n"
            f"_(Note: a **{fee_pct}% transaction fee** is added to the total cost)_"
        )
        return
    new_price, fee = result
    user = await db.get_user(str(interaction.user.id))
    fee_pct_val = await db.get_bot_setting("transaction_fee_pct", "2")
    embed = discord.Embed(
        title="✅ Purchase Successful",
        color=discord.Color.green(),
        description=(
            f"You bought **{amount:,} shares** of **{ticker}** ({stock['name']}) "
            f"at {fmt_money(stock['price'])} each.\n"
            f"**Base cost:** {fmt_money(cost)}\n"
            f"**Transaction fee ({fee_pct_val}% per share):** -{fmt_money(fee)}\n"
            f"**Total spent:** {fmt_money(cost + fee)}\n"
            f"**Remaining cash:** {fmt_money(user['cash'])}\n"
            f"**New market price:** 📈 {fmt_money(new_price)}"
        ),
    )
    await interaction.followup.send(embed=embed)


# ── /sell ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="sell", description="Sell shares of a stock.")
@app_commands.describe(ticker="Stock ticker symbol (e.g. DUCK)", amount="Number of shares to sell")
async def sell_cmd(interaction: discord.Interaction, ticker: str, amount: int):
    await interaction.response.defer()
    await ensure(interaction)
    if amount <= 0:
        await interaction.followup.send("❌ Amount must be a positive number.")
        return
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.followup.send(f"❌ No stock with ticker **{ticker}** found. Use `/stocks` to see available stocks.")
        return
    result = await db.sell_stock(str(interaction.user.id), ticker, amount, stock["price"])
    if result == "cooldown":
        await interaction.followup.send("⏳ You must wait **2 minutes** after buying before selling.")
        return
    if result == "insufficient_shares":
        holding = await db.get_holding(str(interaction.user.id), ticker)
        owned = holding["shares"] if holding else 0
        await interaction.followup.send(
            f"❌ You don't have enough shares of **{ticker}**.\n"
            f"**Requested:** {amount:,}\n**You own:** {owned:,}"
        )
        return
    new_price, net_proceeds, fee = result
    gross = amount * stock["price"]
    user = await db.get_user(str(interaction.user.id))
    fee_pct_val = await db.get_bot_setting("transaction_fee_pct", "2")
    embed = discord.Embed(
        title="✅ Sale Successful",
        color=discord.Color.blue(),
        description=(
            f"You sold **{amount:,} shares** of **{ticker}** ({stock['name']}) "
            f"at {fmt_money(stock['price'])} each.\n"
            f"**Gross proceeds:** {fmt_money(gross)}\n"
            f"**Transaction fee ({fee_pct_val}% per share):** -{fmt_money(fee)}\n"
            f"**Net proceeds:** {fmt_money(net_proceeds)}\n"
            f"**New cash balance:** {fmt_money(user['cash'])}\n"
            f"**New market price:** 📉 {fmt_money(new_price)}"
        ),
    )
    await interaction.followup.send(embed=embed)


# ── /chart ──────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="chart", description="Show the price history chart for a stock.")
@app_commands.describe(ticker="Stock ticker symbol (e.g. DUCK)")
async def chart_cmd(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.followup.send(f"❌ No stock with ticker **{ticker}** found.")
        return
    history = await db.get_price_history(ticker, limit=40)
    if len(history) < 2:
        await interaction.followup.send(
            f"📊 **{ticker}** — {stock['name']}\n"
            f"Current price: {fmt_money(stock['price'])}\n\n"
            f"Not enough price history yet."
        )
        return
    prices = [row["price"] for row in history]
    owners = await db.get_owners_of_stock(ticker)
    base_price = stock["base_price"] if stock["base_price"] > 0 else prices[0]
    buf = render_chart_image(prices, ticker, stock["name"], shareholders=len(owners), base_price=base_price)
    await interaction.followup.send(file=discord.File(buf, filename=f"{ticker}_chart.png"))


@bot.tree.command(name="leaderboard", description="Show the top 10 richest players.")
async def leaderboard_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await db.get_leaderboard(10)

    if not rows:
        await interaction.followup.send("No users yet! Use `/portfolio` to register.")
        return

    embed = discord.Embed(
        title="🏆 Duck Exchange Leaderboard",
        color=discord.Color.gold(),
    )
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        net_worth = row["cash"] + row["bank"] + row["holdings_value"]
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        lines.append(f"{medal} **{row['username']}** — {fmt_money(net_worth)}")
    embed.description = "\n".join(lines)
    await interaction.followup.send(embed=embed)


# ── /marketsummary ────────────────────────────────────────────────────

@bot.tree.command(name="marketsummary", description="Show biggest movers in the last 24 hours.")
async def marketsummary_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await db.get_market_summary()
    if not rows:
        await interaction.followup.send("No stocks yet! Use `/createstock` to add one.")
        return
    embed = discord.Embed(title="📊 Market Summary — 24h Movers", color=discord.Color.gold())
    lines = []
    for row in rows:
        arrow = "📈" if row["change"] > 0 else ("📉" if row["change"] < 0 else "➡️")
        sign = "+" if row["change"] >= 0 else ""
        lines.append(
            f"{arrow} **{row['ticker']}** — {fmt_money(row['price'])}  "
            f"({sign}{fmt_money(row['change'])}, {sign}{row['pct']}%)"
        )
    embed.description = "\n".join(lines) if lines else "No data yet."
    embed.set_footer(text="Changes measured over the last 24 hours")
    await interaction.followup.send(embed=embed)


# ── Admin: /createstock ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="createstock", description="[Admin] Create a new stock.")
@app_commands.describe(
    ticker="Short ticker symbol (e.g. DUCK)",
    name="Full stock name (e.g. Duck Inc.)",
    starting_price="Starting price per share",
    min_change="Min change magnitude per fluctuation (default 0)",
    max_change="Max change magnitude per fluctuation (default 300)",
    fluctuation_minutes="How often this stock fluctuates in minutes (default 1)",
    max_shares="Total shares that can ever exist (default 50)",
)
async def createstock_cmd(
    interaction: discord.Interaction,
    ticker: str,
    name: str,
    starting_price: float,
    min_change: float = 0.0,
    max_change: float = 300.0,
    fluctuation_minutes: int = 1,
    max_shares: int = 50,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can create stocks.", ephemeral=True)
        return
    if starting_price <= 0:
        await interaction.response.send_message("❌ Starting price must be greater than 0.", ephemeral=True)
        return
    if min_change < 0 or max_change < 0:
        await interaction.response.send_message("❌ Change values must be non-negative.", ephemeral=True)
        return
    if min_change > max_change:
        await interaction.response.send_message("❌ min_change cannot exceed max_change.", ephemeral=True)
        return
    if fluctuation_minutes < 1:
        await interaction.response.send_message("❌ Fluctuation interval must be at least 1 minute.", ephemeral=True)
        return
    if max_shares < 1:
        await interaction.response.send_message("❌ max_shares must be at least 1.", ephemeral=True)
        return
    ticker = ticker.upper()
    success = await db.create_stock(ticker, name, starting_price, min_change, max_change, fluctuation_minutes, max_shares)
    if not success:
        await interaction.response.send_message(
            f"❌ A stock with ticker **{ticker}** already exists.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="✅ Stock Created",
        color=discord.Color.green(),
        description=(
            f"**{ticker}** — {name}\n"
            f"**Starting price:** {fmt_money(starting_price)}\n"
            f"**Change range:** ${min_change:,.0f}–${max_change:,.0f}\n"
            f"**Fluctuates every:** {fluctuation_minutes} min\n"
            f"**Total supply:** {max_shares:,} shares"
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── Admin: /editstock ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="editstock", description="[Admin] Edit a stock's name or volatility settings.")
@app_commands.describe(
    ticker="Stock ticker to edit",
    name="New name (leave blank to keep)",
    min_change="New minimum change magnitude (leave blank to keep)",
    max_change="New maximum change magnitude (leave blank to keep)",
    fluctuation_minutes="New fluctuation interval in minutes (leave blank to keep)",
)
async def editstock_cmd(
    interaction: discord.Interaction,
    ticker: str,
    name: str = None,
    min_change: float = None,
    max_change: float = None,
    fluctuation_minutes: int = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can edit stocks.", ephemeral=True)
        return
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.response.send_message(f"❌ No stock with ticker **{ticker}** found.", ephemeral=True)
        return
    updates = {}
    if name is not None:
        updates["name"] = name
    if min_change is not None:
        if min_change < 0:
            await interaction.response.send_message("❌ min_change must be non-negative.", ephemeral=True)
            return
        updates["min_change"] = min_change
    if max_change is not None:
        if max_change < 0:
            await interaction.response.send_message("❌ max_change must be non-negative.", ephemeral=True)
            return
        updates["max_change"] = max_change
    if fluctuation_minutes is not None:
        if fluctuation_minutes < 1:
            await interaction.response.send_message("❌ Interval must be at least 1 minute.", ephemeral=True)
            return
        updates["fluctuation_minutes"] = fluctuation_minutes
    if not updates:
        await interaction.response.send_message("❌ Provide at least one field to update.", ephemeral=True)
        return
    eff_min = updates.get("min_change", stock["min_change"])
    eff_max = updates.get("max_change", stock["max_change"])
    if eff_min > eff_max:
        await interaction.response.send_message("❌ min_change cannot exceed max_change.", ephemeral=True)
        return
    ok = await db.edit_stock(ticker, **updates)
    if not ok:
        await interaction.response.send_message("❌ Failed to update stock.", ephemeral=True)
        return
    updated = await db.get_stock(ticker)
    embed = discord.Embed(
        title=f"✏️ Stock Updated: {ticker}",
        color=discord.Color.gold(),
        description=(
            f"**{updated['ticker']}** — {updated['name']}\n"
            f"**Price:** {fmt_money(updated['price'])}  ·  **Base:** {fmt_money(updated['base_price'])}\n"
            f"**Change range:** ${updated['min_change']:,.0f}–${updated['max_change']:,.0f}\n"
            f"**Fluctuates every:** {updated['fluctuation_minutes']} min"
        ),
    )
    embed.set_footer(text="Updated: " + ", ".join(updates.keys()))
    await interaction.response.send_message(embed=embed)


async def setprice_cmd(interaction: discord.Interaction, ticker: str, new_price: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can change stock prices.", ephemeral=True)
        return

    if new_price <= 0:
        await interaction.response.send_message("❌ Price must be greater than 0.", ephemeral=True)
        return

    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.response.send_message(f"❌ No stock with ticker **{ticker}** found.", ephemeral=True)
        return

    old_price = stock["price"]
    await db.set_stock_price(ticker, new_price)

    owners = await db.get_owners_of_stock(ticker)
    change = new_price - old_price
    direction = "📈 gained" if change >= 0 else "📉 lost"

    embed = discord.Embed(
        title=f"💹 Price Updated: {ticker}",
        color=discord.Color.green() if change >= 0 else discord.Color.red(),
        description=(
            f"**{ticker}** ({stock['name']}) price changed:\n"
            f"{fmt_money(old_price)} → **{fmt_money(new_price)}**\n\n"
            f"**{len(owners)} shareholder(s)** {direction} {fmt_money(abs(change))} per share."
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── Admin: /addprice ──────────────────────────────────────────────────────────

@bot.tree.command(name="addprice", description="[Admin] Increase a stock's price by an amount.")
@app_commands.describe(ticker="Stock ticker symbol", amount="Amount to add to the price")
async def addprice_cmd(interaction: discord.Interaction, ticker: str, amount: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can change stock prices.", ephemeral=True)
        return

    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.response.send_message(f"❌ No stock with ticker **{ticker}** found.", ephemeral=True)
        return

    old_price = stock["price"]
    new_price = old_price + amount

    if new_price <= 0:
        await interaction.response.send_message(
            f"❌ This would set the price below or equal to $0 ({fmt_money(new_price)}). Reduce the amount.", ephemeral=True
        )
        return

    await db.set_stock_price(ticker, new_price)
    owners = await db.get_owners_of_stock(ticker)

    embed = discord.Embed(
        title=f"📈 Price Increased: {ticker}",
        color=discord.Color.green(),
        description=(
            f"**{ticker}** ({stock['name']}) price increased:\n"
            f"{fmt_money(old_price)} → **{fmt_money(new_price)}** (+{fmt_money(amount)})\n\n"
            f"**{len(owners)} shareholder(s)** gained {fmt_money(amount)} per share."
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── Admin: /removeprice ───────────────────────────────────────────────────────

@bot.tree.command(name="removeprice", description="[Admin] Decrease a stock's price by an amount.")
@app_commands.describe(ticker="Stock ticker symbol", amount="Amount to subtract from the price")
async def removeprice_cmd(interaction: discord.Interaction, ticker: str, amount: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can change stock prices.", ephemeral=True)
        return

    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.response.send_message(f"❌ No stock with ticker **{ticker}** found.", ephemeral=True)
        return

    old_price = stock["price"]
    new_price = old_price - amount

    if new_price <= 0:
        await interaction.response.send_message(
            f"❌ This would set the price below or equal to $0 ({fmt_money(new_price)}). Reduce the amount.", ephemeral=True
        )
        return

    await db.set_stock_price(ticker, new_price)
    owners = await db.get_owners_of_stock(ticker)

    embed = discord.Embed(
        title=f"📉 Price Decreased: {ticker}",
        color=discord.Color.red(),
        description=(
            f"**{ticker}** ({stock['name']}) price decreased:\n"
            f"{fmt_money(old_price)} → **{fmt_money(new_price)}** (-{fmt_money(amount)})\n\n"
            f"**{len(owners)} shareholder(s)** lost {fmt_money(amount)} per share."
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── Admin: /deletestock ───────────────────────────────────────────────────────

@bot.tree.command(name="deletestock", description="[Admin] Permanently delete a stock and wipe all holdings.")
@app_commands.describe(ticker="Ticker symbol of the stock to delete")
async def deletestock_cmd(interaction: discord.Interaction, ticker: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can delete stocks.", ephemeral=True)
        return

    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.response.send_message(f"❌ No stock with ticker **{ticker}** found.", ephemeral=True)
        return

    owners = await db.get_owners_of_stock(ticker)
    owner_count = len(owners)

    success = await db.delete_stock(ticker)
    if not success:
        await interaction.response.send_message("❌ Something went wrong while deleting the stock.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🗑️ Stock Deleted",
        color=discord.Color.dark_red(),
        description=(
            f"**{ticker}** ({stock['name']}) has been permanently removed.\n"
            f"All price history erased.\n"
            f"**{owner_count} shareholder(s)** had their holdings wiped."
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── /roulette ─────────────────────────────────────────────────────────────────

ROULETTE_RED = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
ROULETTE_COUNTDOWN = 15  # seconds players have to join

# channel_id -> {"players": [...], "task": asyncio.Task, "message": discord.Message}
active_roulette_games: dict[int, dict] = {}


def roulette_payout(bet: str, spin: int) -> tuple[float, str]:
    """Return (multiplier, result_description). multiplier=0 means loss, -1 invalid."""
    bet = bet.lower().strip()
    if bet.lstrip("-").isdigit():
        num = int(bet)
        if 0 <= num <= 36 and num == spin:
            return 36.0, f"**{spin}** — exact hit! (36×)"
        return 0.0, f"**{spin}** — no match."
    if bet == "red":
        return (2.0, f"**{spin} 🔴** — red wins!") if spin in ROULETTE_RED else (0.0, f"**{spin}** — not red.")
    if bet == "black":
        return (2.0, f"**{spin} ⚫** — black wins!") if (spin != 0 and spin not in ROULETTE_RED) else (0.0, f"**{spin}** — not black.")
    if bet == "odd":
        return (2.0, f"**{spin}** — odd wins!") if (spin != 0 and spin % 2 == 1) else (0.0, f"**{spin}** — not odd.")
    if bet == "even":
        return (2.0, f"**{spin}** — even wins!") if (spin != 0 and spin % 2 == 0) else (0.0, f"**{spin}** — not even.")
    if bet in ("low", "1-18"):
        return (2.0, f"**{spin}** — low wins!") if 1 <= spin <= 18 else (0.0, f"**{spin}** — not low.")
    if bet in ("high", "19-36"):
        return (2.0, f"**{spin}** — high wins!") if 19 <= spin <= 36 else (0.0, f"**{spin}** — not high.")
    return (-1.0, "invalid")


def build_lobby_embed(players: list[dict], seconds_left: int) -> discord.Embed:
    """Build the waiting-room embed shown while players are joining."""
    lines = []
    for p in players:
        lines.append(f"• **{p['username']}** — {fmt_money(p['amount'])} on `{p['bet']}`")
    embed = discord.Embed(
        title="🎰 Roulette — Place Your Bets!",
        color=discord.Color.gold(),
        description=(
            f"⏳ Spinning in **{seconds_left} seconds** — use `/roulette` to join!\n\n"
            + "\n".join(lines)
        ),
    )
    embed.set_footer(text=f"{len(players)} player(s) in • Bets: red, black, odd, even, 1-18, 19-36, or a number 0–36")
    return embed


async def resolve_roulette(channel_id: int) -> None:
    """Wait the countdown then spin and pay out all players."""
    await asyncio.sleep(ROULETTE_COUNTDOWN)

    game = active_roulette_games.pop(channel_id, None)
    if not game:
        return

    spin = random.randint(0, 36)
    wheel_color = "🔴" if spin in ROULETTE_RED else ("🟢" if spin == 0 else "⚫")

    result_lines = []
    async with aiosqlite.connect(db.DB_PATH) as _db:
        for p in game["players"]:
            multiplier, result_desc = roulette_payout(p["bet"], spin)
            won = multiplier > 0
            if won:
                winnings = p["amount"] * multiplier  # add back stake + profit
                await _db.execute(
                    "UPDATE users SET cash = cash + ? WHERE user_id = ?",
                    (winnings, p["user_id"]),
                )
                net = winnings - p["amount"]
                result_lines.append(
                    f"✅ **{p['username']}** bet {fmt_money(p['amount'])} on `{p['bet']}` → 🎉 +{fmt_money(net)}"
                )
            else:
                # Stake already deducted on join — nothing to do
                result_lines.append(
                    f"❌ **{p['username']}** bet {fmt_money(p['amount'])} on `{p['bet']}` → 💸 lost"
                )
        await _db.commit()

    embed = discord.Embed(
        title=f"{wheel_color} The wheel landed on **{spin}**!",
        color=discord.Color.green() if any(roulette_payout(p["bet"], spin)[0] > 0 for p in game["players"]) else discord.Color.red(),
        description="\n".join(result_lines) or "No players.",
    )
    _, spin_desc = roulette_payout(game["players"][0]["bet"] if game["players"] else "red", spin)
    embed.set_footer(text=spin_desc.strip("*").strip())

    try:
        await game["message"].edit(embed=embed)
    except Exception:
        pass


@bot.tree.command(name="roulette", description="Join the global roulette — first bet starts a 15-second window for others to join!")
@app_commands.describe(
    amount="Amount of cash to bet",
    bet="What to bet on: red, black, odd, even, 1-18, 19-36, or a number (0–36)",
)

async def roulette_cmd(interaction: discord.Interaction, amount: float, bet: str):
    await ensure(interaction)
    channel_id = interaction.channel_id

    if amount <= 0:
        await interaction.response.send_message("❌ Bet amount must be greater than $0.", ephemeral=True)
        return

    multiplier, _ = roulette_payout(bet, 0)
    if multiplier == -1.0:
        await interaction.response.send_message(
            "❌ Invalid bet. Choose: `red`, `black`, `odd`, `even`, `1-18`, `19-36`, or a number `0`–`36`.",
            ephemeral=True,
        )
        return

    user = await db.get_user(str(interaction.user.id))
    if user["cash"] < amount:
        await interaction.response.send_message(
            f"❌ You only have {fmt_money(user['cash'])} — not enough to bet {fmt_money(amount)}.",
            ephemeral=True,
        )
        return

    # ── Joining an existing game ───────────────────────────────────────────────
    if channel_id in active_roulette_games:
        game = active_roulette_games[channel_id]

        if any(p["user_id"] == str(interaction.user.id) for p in game["players"]):
            await interaction.response.send_message("⚠️ You're already in this game!", ephemeral=True)
            return

        # Deduct stake immediately so funds are locked in
        async with aiosqlite.connect(db.DB_PATH) as _db:
            await _db.execute(
                "UPDATE users SET cash = cash - ? WHERE user_id = ?",
                (amount, str(interaction.user.id)),
            )
            await _db.commit()

        game["players"].append({
            "user_id": str(interaction.user.id),
            "username": interaction.user.display_name,
            "amount": amount,
            "bet": bet.lower().strip(),
        })

        try:
            await game["message"].edit(embed=build_lobby_embed(game["players"], ROULETTE_COUNTDOWN))
        except Exception:
            pass

        await interaction.response.send_message(
            f"✅ **{interaction.user.display_name}** joined the game! Bet: {fmt_money(amount)} on `{bet}`",
            ephemeral=False,
        )
        return

    # ── Starting a new game ───────────────────────────────────────────────────
    # Deduct stake immediately
    async with aiosqlite.connect(db.DB_PATH) as _db:
        await _db.execute(
            "UPDATE users SET cash = cash - ? WHERE user_id = ?",
            (amount, str(interaction.user.id)),
        )
        await _db.commit()

    players = [{
        "user_id": str(interaction.user.id),
        "username": interaction.user.display_name,
        "amount": amount,
        "bet": bet.lower().strip(),
    }]

    await interaction.response.send_message(embed=build_lobby_embed(players, ROULETTE_COUNTDOWN))
    lobby_msg = await interaction.original_response()

    task = asyncio.create_task(resolve_roulette(channel_id))
    active_roulette_games[channel_id] = {
        "players": players,
        "task": task,
        "message": lobby_msg,
    }


# ── /givecash ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="givecash", description="[Admin] Give wallet cash to a player.")
@app_commands.describe(user="Target user", amount="Amount to give")
async def givecash_cmd(interaction: discord.Interaction, user: discord.Member, amount: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    await db.ensure_user(str(user.id), user.display_name)
    ok = await db.admin_give_cash(str(user.id), amount)
    if not ok:
        await interaction.response.send_message("❌ User not found.", ephemeral=True)
        return
    target = await db.get_user(str(user.id))
    embed = discord.Embed(
        title="💸 Cash Given",
        color=discord.Color.green(),
        description=f"Gave **{fmt_money(amount)}** to **{user.display_name}**.\nTheir new wallet: {fmt_money(target['cash'])}",
    )
    await interaction.response.send_message(embed=embed)



# ── /givebank ────────────────────────────────────────────────────────────────

@bot.tree.command(name="givebank", description="[Admin] Give bank cash to a player (goes straight to bank).")
@app_commands.describe(user="Target user", amount="Amount to give")
async def givebank_cmd(interaction: discord.Interaction, user: discord.Member, amount: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    await db.ensure_user(str(user.id), user.display_name)
    ok = await db.admin_give_bank(str(user.id), amount)
    if not ok:
        await interaction.response.send_message("❌ User not found.", ephemeral=True)
        return
    target = await db.get_user(str(user.id))
    embed = discord.Embed(
        title="🏦 Bank Cash Given",
        color=discord.Color.green(),
        description=f"Gave **{fmt_money(amount)}** to **{user.display_name}**'s bank.\nTheir new bank balance: {fmt_money(target['bank'])}",
    )
    await interaction.response.send_message(embed=embed)


# ── /removecash ───────────────────────────────────────────────────────────────

@bot.tree.command(name="removecash", description="[Admin] Remove wallet cash from a player.")
@app_commands.describe(user="Target user", amount="Amount to remove")
async def removecash_cmd(interaction: discord.Interaction, user: discord.Member, amount: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    await db.ensure_user(str(user.id), user.display_name)
    result = await db.admin_remove_cash(str(user.id), amount)
    if result == "user_not_found":
        await interaction.response.send_message("❌ User not found.", ephemeral=True)
        return
    if result == "insufficient_funds":
        target = await db.get_user(str(user.id))
        await interaction.response.send_message(
            f"❌ **{user.display_name}** only has {fmt_money(target['cash'])} in wallet.", ephemeral=True
        )
        return
    target = await db.get_user(str(user.id))
    embed = discord.Embed(
        title="💸 Cash Removed",
        color=discord.Color.red(),
        description=f"Removed **{fmt_money(amount)}** from **{user.display_name}**.\nTheir new wallet: {fmt_money(target['cash'])}",
    )
    await interaction.response.send_message(embed=embed)


# ── /balance ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="globaldep", description="[Admin] Shift ALL stock prices by a fixed amount (negative to decrease).")
@app_commands.describe(amount="Dollar amount to add to every stock price (use negative to decrease)")
async def globaldep_cmd(interaction: discord.Interaction, amount: float):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if amount == 0:
        await interaction.response.send_message("❌ Amount cannot be zero.", ephemeral=True)
        return
    count = await db.global_dep(amount)
    direction = "U0001f4c8 increased" if amount > 0 else "U0001f4c9 decreased"
    embed = discord.Embed(
        title="U0001f310 Global Market Shift",
        color=discord.Color.green() if amount > 0 else discord.Color.red(),
        description=f"All **{count}** stocks have been {direction} by **{fmt_money(abs(amount))}**.",
    )
    embed.set_footer(text=f"Triggered by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)


@bot.command(name="globaldep", aliases=["gdep"])
async def prefix_globaldep(ctx, amount: float = None):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Only server administrators can use this command.", delete_after=8)
        return
    if amount is None:
        await ctx.send("❌ Usage: ?globaldep <amount>  (negative = decrease all stocks)", delete_after=8)
        return
    if amount == 0:
        await ctx.send("❌ Amount cannot be zero.", delete_after=8)
        return
    count = await db.global_dep(amount)
    direction = "U0001f4c8 increased" if amount > 0 else "U0001f4c9 decreased"
    embed = discord.Embed(
        title="U0001f310 Global Market Shift",
        color=discord.Color.green() if amount > 0 else discord.Color.red(),
        description=f"All **{count}** stocks have been {direction} by **{fmt_money(abs(amount))}**.",
    )
    embed.set_footer(text=f"Triggered by {ctx.author.display_name}")
    await ctx.send(embed=embed)



# ── /loan ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="loan", description="Borrow cash from the bank (25% interest, due in 20 min).")
@app_commands.describe(amount="How much to borrow")
@app_commands.choices(amount=[
    app_commands.Choice(name="$100", value=100),
    app_commands.Choice(name="$1,000", value=1000),
    app_commands.Choice(name="$10,000", value=10000),
    app_commands.Choice(name="$100,000", value=100000),
])
async def loan_cmd(interaction: discord.Interaction, amount: int):
      await ensure(interaction)
      result = await db.take_loan(str(interaction.user.id), amount)
      if result.get("error") == "has_loan":
          await interaction.response.send_message(
              f"\u274c You already have a loan of **{fmt_money(result['owed'])}** outstanding. Use `/payloan` to repay it.",
              ephemeral=True,
          )
          return
      if result.get("error") in ("invalid_amount", "user_not_found"):
          await interaction.response.send_message("\u274c Invalid request.", ephemeral=True)
          return
      from datetime import datetime
      due_str = datetime.fromisoformat(result["due"]).strftime("%Y-%m-%d %H:%M UTC")
      embed = discord.Embed(
          title="\U0001f3e6 Loan Approved",
          color=discord.Color.green(),
          description=(
              f"Borrowed **{fmt_money(result['borrowed'])}** \u2192 wallet.\n"
              f"You owe **{fmt_money(result['owed'])}** (25% interest).\n"
              f"Due: `{due_str}`\n\n"
              f"\U0001f45b New wallet: **{fmt_money(result['new_cash'])}**"
          ),
      )
      await interaction.response.send_message(embed=embed)


# ── /payloan ───────────────────────────────────────────────────────────────

@bot.tree.command(name="payloan", description="Repay part or all of your outstanding loan.")
@app_commands.describe(amount="Amount to repay from your wallet")
async def payloan_cmd(interaction: discord.Interaction, amount: float):
      await ensure(interaction)
      result = await db.repay_loan(str(interaction.user.id), amount)
      if result.get("error") == "no_loan":
          await interaction.response.send_message("\u274c You have no outstanding loan.", ephemeral=True)
          return
      if result.get("error") == "insufficient_funds":
          await interaction.response.send_message(
              f"\u274c You only have **{fmt_money(result['has'])}** in your wallet.", ephemeral=True
          )
          return
      paid_off = result["remaining"] <= 0
      embed = discord.Embed(
          title="\U0001f4b3 Loan Repayment",
          color=discord.Color.green() if paid_off else discord.Color.orange(),
          description=(
              f"Paid **{fmt_money(result['paid'])}** toward your loan.\n"
              + ("\u2705 Loan fully repaid!" if paid_off else f"Remaining: **{fmt_money(result['remaining'])}**")
              + f"\n\n\U0001f45b New wallet: **{fmt_money(result['new_cash'])}**"
          ),
      )
      await interaction.response.send_message(embed=embed)


# ── ?loan / ?payloan ───────────────────────────────────────────────────────

@bot.command(name="loan")
async def prefix_loan(ctx, amount: float = None):
      if amount is None:
          await ctx.send("\u274c Usage: `?loan <amount>`", delete_after=8)
          return
      await ensure_prefix(ctx)
      result = await db.take_loan(str(ctx.author.id), amount)
      if result.get("error") == "has_loan":
          await ctx.send(f"\u274c Already have a loan of **{fmt_money(result['owed'])}**. Use `?payloan <amount>` to repay.")
          return
      if result.get("error") == "exceeds_limit":
          await ctx.send(f"\u274c Max borrow is your bank balance (**{fmt_money(result['max'])}**).")
          return
      if result.get("error") in ("invalid_amount", "user_not_found"):
          await ctx.send("\u274c Invalid request.")
          return
      from datetime import datetime
      due_str = datetime.fromisoformat(result["due"]).strftime("%Y-%m-%d %H:%M UTC")
      embed = discord.Embed(
          title="\U0001f3e6 Loan Approved",
          color=discord.Color.green(),
          description=(
              f"Borrowed **{fmt_money(result['borrowed'])}** \u2192 wallet.\n"
              f"You owe **{fmt_money(result['owed'])}** (25% interest).\n"
              f"Due: `{due_str}`\n\n"
              f"\U0001f45b New wallet: **{fmt_money(result['new_cash'])}**"
          ),
      )
      await ctx.send(embed=embed)


@bot.command(name="payloan", aliases=["pl", "repay"])
async def prefix_payloan(ctx, amount: float = None):
      if amount is None:
          await ctx.send("\u274c Usage: `?payloan <amount>`", delete_after=8)
          return
      await ensure_prefix(ctx)
      result = await db.repay_loan(str(ctx.author.id), amount)
      if result.get("error") == "no_loan":
          await ctx.send("\u274c You have no outstanding loan.")
          return
      if result.get("error") == "insufficient_funds":
          await ctx.send(f"\u274c You only have **{fmt_money(result['has'])}** in wallet.")
          return
      paid_off = result["remaining"] <= 0
      embed = discord.Embed(
          title="\U0001f4b3 Loan Repayment",
          color=discord.Color.green() if paid_off else discord.Color.orange(),
          description=(
              f"Paid **{fmt_money(result['paid'])}** toward your loan.\n"
              + ("\u2705 Loan fully repaid!" if paid_off else f"Remaining: **{fmt_money(result['remaining'])}**")
              + f"\n\n\U0001f45b New wallet: **{fmt_money(result['new_cash'])}**"
          ),
      )
      await ctx.send(embed=embed)


# ── /announce ─────────────────────────────────────────────────────────────

@bot.tree.command(name="announce", description="[Admin] Post an announcement embed to any channel.")
@app_commands.describe(channel="Channel to post in", message="Announcement text")
async def announce_cmd(interaction: discord.Interaction, channel: discord.TextChannel, message: str):
      if not is_admin(interaction):
          await interaction.response.send_message("\u274c Admins only.", ephemeral=True)
          return
      embed = discord.Embed(
          title="\U0001f4e2 Announcement",
          description=message,
          color=discord.Color.gold(),
      )
      embed.set_footer(text=f"Posted by {interaction.user.display_name}")
      await channel.send(embed=embed)
      await interaction.response.send_message(f"\u2705 Posted to {channel.mention}", ephemeral=True)


@bot.command(name="announce")
async def prefix_announce(ctx, channel: discord.TextChannel = None, *, message: str = ""):
      if not ctx.guild or not ctx.author.guild_permissions.administrator:
          await ctx.send("\u274c Admins only.", delete_after=8)
          return
      if not channel or not message:
          await ctx.send("\u274c Usage: `?announce #channel Your message here`", delete_after=8)
          return
      embed = discord.Embed(
          title="\U0001f4e2 Announcement",
          description=message,
          color=discord.Color.gold(),
      )
      embed.set_footer(text=f"Posted by {ctx.author.display_name}")
      await channel.send(embed=embed)
      try:
          await ctx.message.delete()
      except Exception:
          pass


  
@bot.tree.command(name="balance", description="Check your wallet and bank balance.")
async def balance_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    user = await db.get_user(str(interaction.user.id))
    embed = discord.Embed(
        title=f"💰 {interaction.user.display_name}'s Balance",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="👛 Wallet", value=fmt_money(user["cash"]), inline=True)
    embed.add_field(name="🏦 Bank", value=fmt_money(user["bank"]), inline=True)
    embed.add_field(name="💎 Total", value=fmt_money(user["cash"] + user["bank"]), inline=True)
    if user.get("loan_amount") and user["loan_amount"] > 0:
        from datetime import datetime, timezone
        due = datetime.fromisoformat(user["loan_due"]) if user.get("loan_due") else None
        time_left = int((due - datetime.now(timezone.utc)).total_seconds() / 60) if due else 0
        due_str = f"{max(time_left, 0)} min" if time_left > 0 else "\u26a0\ufe0f OVERDUE"
        embed.add_field(name="\U0001f4b8 Loan Owed", value=f"{fmt_money(user['loan_amount'])} (due in {due_str})", inline=False)
    embed.color = discord.Color.red() if (user.get("loan_amount") and user["loan_amount"] > 0) else discord.Color.blurple()
    embed.set_footer(text="Use /deposit or /withdraw to move money · Wallet is stealable, bank is safe!")
    await interaction.response.send_message(embed=embed)


# ── /deposit ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="deposit", description="Deposit wallet cash into your bank (safe from theft).")
@app_commands.describe(amount="Amount to deposit")
async def deposit_cmd(interaction: discord.Interaction, amount: float):
    await ensure(interaction)
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    result = await db.deposit(str(interaction.user.id), amount)
    if result == "insufficient_funds":
        user = await db.get_user(str(interaction.user.id))
        await interaction.response.send_message(
            f"❌ You only have {fmt_money(user['cash'])} in your wallet.", ephemeral=True
        )
        return
    user = await db.get_user(str(interaction.user.id))
    embed = discord.Embed(
        title="🏦 Deposit Successful",
        color=discord.Color.green(),
        description=(
            f"Deposited **{fmt_money(amount)}** into your bank.\n"
            f"👛 Wallet: {fmt_money(user['cash'])}  |  🏦 Bank: {fmt_money(user['bank'])}"
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── /withdraw ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="withdraw", description="Withdraw cash from your bank to your wallet.")
@app_commands.describe(amount="Amount to withdraw")
async def withdraw_cmd(interaction: discord.Interaction, amount: float):
    await ensure(interaction)
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    user = await db.get_user(str(interaction.user.id))
    actual = min(amount, user["bank"])
    if actual <= 0:
        await interaction.response.send_message("❌ Your bank is empty.", ephemeral=True)
        return
    await db.withdraw(str(interaction.user.id), actual)
    user = await db.get_user(str(interaction.user.id))
    was_partial = actual < amount
    desc = f"Withdrew **{fmt_money(actual)}** from your bank.\n"
    if was_partial:
        desc += f"_(You only had {fmt_money(actual)}, so that amount was withdrawn.)_\n"
    desc += f"👛 Wallet: {fmt_money(user['cash'])}  |  🏦 Bank: {fmt_money(user['bank'])}"
    embed = discord.Embed(
        title="🏦 Withdrawal Successful",
        color=discord.Color.green(),
        description=desc,
    )
    await interaction.response.send_message(embed=embed)


# ── /transfer ─────────────────────────────────────────────────────────────────

@bot.tree.command(name="transfer", description="Send wallet cash to another player.")
@app_commands.describe(user="Who to send to", amount="Amount to transfer")

async def transfer_cmd(interaction: discord.Interaction, user: discord.Member, amount: float):
    await ensure(interaction)
    if user.id == interaction.user.id:
        await interaction.response.send_message("❌ You can't transfer to yourself.", ephemeral=True)
        return
    if amount <= 0:
        await interaction.response.send_message("❌ Amount must be positive.", ephemeral=True)
        return
    await db.ensure_user(str(user.id), user.display_name)
    result = await db.transfer_cash(str(interaction.user.id), str(user.id), amount)
    if result == "insufficient_funds":
        sender = await db.get_user(str(interaction.user.id))
        await interaction.response.send_message(
            f"❌ You only have {fmt_money(sender['cash'])} in your wallet.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="💸 Transfer Sent",
        color=discord.Color.green(),
        description=f"**{interaction.user.display_name}** sent **{fmt_money(amount)}** to **{user.display_name}**.",
    )
    await interaction.response.send_message(embed=embed)


# ── /steal ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="steal", description="Attempt to steal from another player's wallet.")
@app_commands.describe(user="Who to steal from")
async def steal_cmd(interaction: discord.Interaction, user: discord.Member):
    await ensure(interaction)
    if user.id == interaction.user.id:
        await interaction.response.send_message("❌ You can't steal from yourself.", ephemeral=True)
        return
    await db.ensure_user(str(user.id), user.display_name)

    target_data = await db.get_user(str(user.id))
    if target_data["cash"] <= 0:
        await interaction.response.send_message(
            f"❌ **{user.display_name}**'s wallet is empty — nothing to steal!", ephemeral=True
        )
        return

    result = await db.steal_wallet(str(interaction.user.id), str(user.id))

    if "error" in result:
        if result["error"] == "cooldown":
            secs = result["seconds"]
            m, s = divmod(secs, 60)
            await interaction.response.send_message(
                f"⏳ Steal on cooldown! Try again in **{m}m {s}s**.", ephemeral=True
            )
        else:
            await interaction.response.send_message("❌ Something went wrong.", ephemeral=True)
        return

    if result["success"]:
        embed = discord.Embed(
            title="🎉 Theft Successful!",
            color=discord.Color.green(),
            description=(
                f"**{interaction.user.display_name}** stole **{fmt_money(result['stolen'])}** "
                f"from **{user.display_name}**'s wallet!\n\n"
                f"👛 Your new wallet: {fmt_money(result['thief_new_cash'])}"
            ),
        )
    else:
        embed = discord.Embed(
            title="❌ Theft Failed!",
            color=discord.Color.red(),
            description=(
                f"**{interaction.user.display_name}** was caught trying to steal from **{user.display_name}**!\n"
                f"💸 Penalty: **-{fmt_money(result['penalty'])}** (10% of your total wealth)\n\n"
                f"👛 Wallet: {fmt_money(result['thief_new_cash'])}  |  🏦 Bank: {fmt_money(result['thief_new_bank'])}"
            ),
        )
    await interaction.response.send_message(embed=embed)


# ── /claim ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="claim", description="Claim your wallet bonus.")
async def claim_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    result = await db.claim_daily(str(interaction.user.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        time_str = f"{m}m {s}s" if m else f"{s}s"
        cooldown = result.get("cooldown_secs", 60)
        c_m, c_s = divmod(cooldown, 60)
        cooldown_str = f"{c_m}m {c_s}s" if c_m else f"{c_s}s"
        await interaction.response.send_message(
            f"⏳ Already claimed! Come back in **{time_str}**.", ephemeral=True
        )
        return
    reward = result.get("reward", 100)
    cooldown = result.get("cooldown_secs", 60)
    c_m, c_s = divmod(cooldown, 60)
    cooldown_str = f"{c_m}m {c_s}s" if c_m else f"{c_s}s"
    embed = discord.Embed(
        title=f"✅ Claimed {fmt_money(reward)}!",
        color=discord.Color.green(),
        description=f"💵 Added **{fmt_money(reward)}** to your wallet.\n👛 New wallet balance: {fmt_money(result['new_cash'])}",
    )
    embed.set_footer(text=f"You can claim again in {cooldown_str}!")
    await interaction.response.send_message(embed=embed)


# ── /flip ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="flip", description="Flip a coin — 50/50 chance to double your bet!")
@app_commands.describe(amount="Amount to bet", choice="heads or tails")
@app_commands.choices(choice=[
    app_commands.Choice(name="Heads", value="heads"),
    app_commands.Choice(name="Tails", value="tails"),
])

async def flip_cmd(interaction: discord.Interaction, amount: float, choice: str):
    await ensure(interaction)
    if amount <= 0:
        await interaction.response.send_message("❌ Bet must be positive.", ephemeral=True)
        return
    user = await db.get_user(str(interaction.user.id))
    if user["cash"] < amount:
        await interaction.response.send_message(
            f"❌ You only have {fmt_money(user['cash'])} in your wallet.", ephemeral=True
        )
        return

    flip = random.choice(["heads", "tails"])
    won = flip == choice
    net = amount if won else -amount

    async with aiosqlite.connect(db.DB_PATH) as _db:
        await _db.execute(
            "UPDATE users SET cash = cash + ? WHERE user_id = ?",
            (net, str(interaction.user.id)),
        )
        await _db.commit()

    user_after = await db.get_user(str(interaction.user.id))
    coin = "🟡 Heads" if flip == "heads" else "⚫ Tails"
    embed = discord.Embed(
        title=f"{coin}!",
        color=discord.Color.green() if won else discord.Color.red(),
        description=(
            f"You picked **{choice}** and the coin landed on **{flip}**.\n"
            f"{'🎉 You won' if won else '💸 You lost'} **{fmt_money(amount)}**!\n"
            f"👛 New wallet: {fmt_money(user_after['cash'])}"
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── /blackjack ────────────────────────────────────────────────────────────────

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

def card_value(rank: str) -> int:
    if rank in ("J","Q","K"): return 10
    if rank == "A": return 11
    return int(rank)

def hand_value(hand: list[str]) -> int:
    total = sum(card_value(c.split(" ")[0]) for c in hand)
    aces = sum(1 for c in hand if c.startswith("A"))
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

def fmt_hand(hand: list[str], hide_second: bool = False) -> str:
    if hide_second and len(hand) >= 2:
        return f"{hand[0]}, 🂠"
    return "  ".join(hand)

def new_deck() -> list[str]:
    deck = [f"{r} {s}" for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


# In-memory blackjack games: user_id -> game state
active_bj_games: dict[int, dict] = {}


class BlackjackView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This isn't your game!", ephemeral=True)
            return False
        return True

    def _get_game(self):
        return active_bj_games.get(self.user_id)

    async def _update(self, interaction: discord.Interaction, game: dict, finished: bool = False):
        embed = build_bj_embed(game, finished)
        if finished:
            self.clear_items()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        game = self._get_game()
        if not game:
            await interaction.response.defer()
            return
        game["player"].append(game["deck"].pop())
        pv = hand_value(game["player"])
        if pv >= 21:
            await self._finish(interaction, game)
        else:
            await self._update(interaction, game)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def stand_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        game = self._get_game()
        if not game:
            await interaction.response.defer()
            return
        await self._finish(interaction, game)

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.success, emoji="💰")
    async def double_btn(self, interaction: discord.Interaction, _: discord.ui.Button):
        game = self._get_game()
        if not game:
            await interaction.response.defer()
            return
        if len(game["player"]) != 2:
            await interaction.response.send_message("❌ Can only double on first two cards.", ephemeral=True)
            return
        user = await db.get_user(str(interaction.user.id))
        if user["cash"] < game["bet"]:
            await interaction.response.send_message(
                f"❌ Not enough wallet cash to double (need {fmt_money(game['bet'])}).", ephemeral=True
            )
            return
        # Deduct extra bet
        async with aiosqlite.connect(db.DB_PATH) as _db:
            await _db.execute(
                "UPDATE users SET cash = cash - ? WHERE user_id = ?",
                (game["bet"], str(interaction.user.id)),
            )
            await _db.commit()
        game["bet"] *= 2
        game["player"].append(game["deck"].pop())
        await self._finish(interaction, game)

    async def _finish(self, interaction: discord.Interaction, game: dict):
        # Dealer draws until 17+
        while hand_value(game["dealer"]) < 17:
            game["dealer"].append(game["deck"].pop())

        pv = hand_value(game["player"])
        dv = hand_value(game["dealer"])
        bet = game["bet"]

        if pv > 21:
            outcome, net = "bust", -bet
        elif dv > 21 or pv > dv:
            # Check blackjack (natural 21 on first 2 cards)
            if len(game["player"]) == 2 and pv == 21:
                outcome, net = "blackjack", round(bet * 1.5, 2)
            else:
                outcome, net = "win", bet
        elif pv == dv:
            outcome, net = "push", 0.0
        else:
            outcome, net = "lose", -bet

        game["outcome"] = outcome
        game["net"] = net

        async with aiosqlite.connect(db.DB_PATH) as _db:
            # Payout fix:
            # Win = return bet + profit (2x bet)
            # Lose = already lost bet, no extra deduction
            # Push = refund bet

            if game.get("outcome") in ("win", "blackjack"):
                payout = game["bet"] * 2
            elif game.get("outcome") == "push":
                payout = game["bet"]
            else:
                payout = 0

            await _db.execute(
                "UPDATE users SET cash = cash + ? WHERE user_id = ?",
                (payout, str(interaction.user.id)),
            )
            await _db.commit()

        active_bj_games.pop(self.user_id, None)
        await self._update(interaction, game, finished=True)

    async def on_timeout(self):
        game = active_bj_games.pop(self.user_id, None)
        if game:
            # Refund bet on timeout
            async with aiosqlite.connect(db.DB_PATH) as _db:
                await _db.execute(
                    "UPDATE users SET cash = cash + ? WHERE user_id = ?",
                    (game["bet"], str(self.user_id)),
                )
                await _db.commit()


def build_bj_embed(game: dict, finished: bool = False) -> discord.Embed:
    pv = hand_value(game["player"])
    dv = hand_value(game["dealer"])
    outcome = game.get("outcome")
    net = game.get("net", 0.0)

    if outcome == "blackjack":
        title, color = "🎰 BLACKJACK! 21!", discord.Color.gold()
    elif outcome == "win":
        title, color = "✅ You Win!", discord.Color.green()
    elif outcome == "bust":
        title, color = "💥 Bust! You Lose.", discord.Color.red()
    elif outcome == "lose":
        title, color = "❌ Dealer Wins.", discord.Color.red()
    elif outcome == "push":
        title, color = "🤝 Push — Tie!", discord.Color.blurple()
    else:
        title, color = "🃏 Blackjack", discord.Color.dark_green()

    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name=f"Your Hand ({pv})",
        value=fmt_hand(game["player"]),
        inline=False,
    )

    if finished:
        embed.add_field(
            name=f"Dealer's Hand ({dv})",
            value=fmt_hand(game["dealer"]),
            inline=False,
        )
    else:
        embed.add_field(
            name=f"Dealer's Hand ({card_value(game['dealer'][0].split()[0])}+?)",
            value=fmt_hand(game["dealer"], hide_second=True),
            inline=False,
        )

    embed.add_field(name="Bet", value=fmt_money(game["bet"]), inline=True)

    if finished and outcome:
        result_str = (
            f"+{fmt_money(game['bet'] * 2)}"
            if outcome in ("win", "blackjack")
            else f"+{fmt_money(game['bet'])}"
            if outcome == "push"
            else f"-{fmt_money(game['bet'])}"
        )
        embed.add_field(name="Result", value=result_str, inline=True)

    return embed

@bot.tree.command(name="blackjack", description="Play blackjack against the dealer!")
@app_commands.describe(amount="Amount to bet from your wallet")

async def blackjack_cmd(interaction: discord.Interaction, amount: float):
    await ensure(interaction)
    if amount <= 0:
        await interaction.response.send_message("❌ Bet must be positive.", ephemeral=True)
        return
    if interaction.user.id in active_bj_games:
        await interaction.response.send_message("❌ You already have a blackjack game in progress!", ephemeral=True)
        return

    user = await db.get_user(str(interaction.user.id))
    if user["cash"] < amount:
        await interaction.response.send_message(
            f"❌ You only have {fmt_money(user['cash'])} in your wallet.", ephemeral=True
        )
        return

    # Deduct bet upfront
    async with aiosqlite.connect(db.DB_PATH) as _db:
        await _db.execute(
            "UPDATE users SET cash = cash - ? WHERE user_id = ?",
            (amount, str(interaction.user.id)),
        )
        await _db.commit()

    deck = new_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    game = {"deck": deck, "player": player, "dealer": dealer, "bet": amount}
    active_bj_games[interaction.user.id] = game

    pv = hand_value(player)
    view = BlackjackView(interaction.user.id)

    # Instant blackjack check
    if pv == 21:
        # Dealer draws
        while hand_value(dealer) < 17:
            dealer.append(deck.pop())
        dv = hand_value(dealer)
        if dv == 21:
            game["outcome"], game["net"] = "push", 0.0
        else:
            game["outcome"], game["net"] = "blackjack", round(amount * 1.5, 2)
        async with aiosqlite.connect(db.DB_PATH) as _db:
            await _db.execute(
                "UPDATE users SET cash = cash + ? WHERE user_id = ?",
                (amount + game["net"], str(interaction.user.id)),
            )
            await _db.commit()
        active_bj_games.pop(interaction.user.id, None)
        await interaction.response.send_message(embed=build_bj_embed(game, finished=True))
        return

    await interaction.response.send_message(embed=build_bj_embed(game), view=view)


# ── /work ─────────────────────────────────────────────────────────────────────


WORK_MESSAGES = [
    "You worked a 9-to-5 at McDonald's flipping burgers",
    "You delivered pizzas in a thunderstorm on a bicycle",
    "You typed 'synergy' 47 times in a pointless meeting",
    "You fixed a printer that was never broken to begin with",
    "You sat through a 3-hour meeting that could've been an email",
    "You collected shopping carts at Walmart in 95° heat",
    "You drove Uber for 8 hours and got tipped $2",
    "You coded something that kind of works if you squint",
    "You cleaned up after a kids' birthday party",
    "You worked the overnight shift at a gas station",
    "You made coffee for your boss for the 300th time this year",
    "You fixed the WiFi by turning it off and on again",
    "You assembled 47 IKEA chairs with no instructions",
    "You answered 200 emails that nobody needed",
    "You stacked shelves at 3am while someone mopped around you",
    "You wrote a report that will never be read by anyone",
    "You stood at a cash register for 8 hours straight",
    "You walked someone's dog and it dragged you into a bush",
    "You tutored a kid who already knew more than you",
    "You sold insurance over the phone for 6 hours",
]

CRIME_SUCCESS_MESSAGES = [
    "You robbed a pizza place and got away with the register",
    "You pickpocketed a guy who was already pickpocketing someone else",
    "You sold knock-off designer bags outside the mall",
    "You hacked someone's Netflix and sold their password",
    "You ran a pyramid scheme that somehow actually worked",
    "You counterfeited Monopoly money and a store accepted it",
    "You held a fake raffle and kept all the tickets",
    "You scammed a scammer — respect",
    "You stole copper wire from a construction site",
    "You sold a bridge to a tourist",
    "You forged a gift card and nobody noticed",
    "You ran a fake 'distressed duck' charity",
    "You plagiarized someone's NFT",
    "You dined and dashed at a 5-star restaurant",
    "You bootlegged a movie that was already free",
]

CRIME_FAIL_MESSAGES = [
    "You tried to rob a bank but forgot to load the water gun 💦",
    "You got caught shoplifting a single grape 🍇",
    "The pizza place fought back with a rolling pin 🍕",
    "You dropped YOUR wallet while stealing someone else's 👛",
    "An undercover cop bought all your fake bags 👜",
    "You tried to pickpocket a martial arts instructor 🥋",
    "Your getaway car had a flat tire 🚗",
    "You butt-dialed 911 mid-heist 📱",
    "Security recognized you from last time 😬",
    "Your mask fell off immediately 🎭",
    "You tripped running away and went viral on TikTok 📹",
    "The ATM fought back 🏧",
]


# ── /work ──────────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="work", description="Work a job and earn cash (3 min cooldown).")
async def work_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    result = await db.do_work(str(interaction.user.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        await interaction.response.send_message(
            f"⏳ You're too tired to work! Rest for **{m}m {s}s**.", ephemeral=True
        )
        return
    work_min = await db.get_bot_setting("work_min", "50")
    work_max = await db.get_bot_setting("work_max", "200")
    msg = random.choice(WORK_MESSAGES)
    embed = discord.Embed(
        title="💼 Work Complete",
        color=discord.Color.blue(),
        description=(
            f"{msg} and earned **{fmt_money(result['earned'])}**!\n\n"
            f"👛 New wallet: {fmt_money(result['new_cash'])}"
        ),
    )
    embed.set_footer(text=f"Cooldown: 3 min  ·  Payout: ${work_min}–${work_max}")
    await interaction.response.send_message(embed=embed)


# ── /crime ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="crime", description="Commit a crime for a cash reward (cooldown and risk apply).")
async def crime_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    result = await db.do_crime(str(interaction.user.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        await interaction.response.send_message(
            f"⏳ Laying low after last time. Try again in **{m}m {s}s**.", ephemeral=True
        )
        return
    crime_min = await db.get_bot_setting("crime_min", "100")
    crime_max = await db.get_bot_setting("crime_max", "500")
    crime_fail_pct = await db.get_bot_setting("crime_fail_pct", "30")
    if result["success"]:
        msg = random.choice(CRIME_SUCCESS_MESSAGES)
        embed = discord.Embed(
            title="🦹 Crime Successful",
            color=discord.Color.green(),
            description=(
                f"{msg} and pocketed **{fmt_money(result['earned'])}**!\n\n"
                f"👛 New wallet: {fmt_money(result['new_cash'])}"
            ),
        )
    else:
        msg = random.choice(CRIME_FAIL_MESSAGES)
        embed = discord.Embed(
            title="🚔 Busted!",
            color=discord.Color.red(),
            description=(
                f"{msg}\n\n"
                f"💸 You lost **{fmt_money(result['penalty'])}** ({crime_fail_pct}% of your wealth) in fines!\n"
                f"👛 Wallet: {fmt_money(result['new_cash'])}  |  🏦 Bank: {fmt_money(result['new_bank'])}"
            ),
        )
    embed.set_footer(text=f"Cooldown: 5 min  ·  Payout: ${crime_min}–${crime_max}  ·  Fail: {crime_fail_pct}% wealth")
    await interaction.response.send_message(embed=embed)


# ── Admin: /setconfig ───────────────────────────────────────────────────────────────────

VALID_CONFIG_KEYS = {
    "work_min": "Minimum work payout ($)",
    "work_max": "Maximum work payout ($)",
    "crime_min": "Minimum crime payout ($)",
    "crime_max": "Maximum crime payout ($)",
    "crime_fail_pct": "% of wealth lost on crime fail (e.g. 30 = 30%)",
    "steal_fail_pct": "% of wealth lost on failed steal (e.g. 10 = 10%)",
    "steal_success_rate": "% chance steal succeeds (e.g. 30 = 30%)",
    "transaction_fee_pct": "% fee on stock buy/sell (e.g. 2 = 2%)",
    "claim_reward": "Cash given per /claim (e.g. 100)",
    "claim_cooldown_secs": "Seconds between /claim uses (e.g. 60)",
}


@bot.tree.command(name="setconfig", description="[Admin] Adjust server economy settings.")
@app_commands.describe(
    key="Setting to change (see /config for all keys)",
    value="New numeric value",
)
async def setconfig_cmd(interaction: discord.Interaction, key: str, value: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if key not in VALID_CONFIG_KEYS:
        keys_list = "\n".join(f"• `{k}` — {v}" for k, v in VALID_CONFIG_KEYS.items())
        await interaction.response.send_message(
            f"❌ Unknown key **{key}**.\n\n**Valid keys:**\n{keys_list}", ephemeral=True
        )
        return
    try:
        num = float(value)
        if num < 0:
            raise ValueError
    except ValueError:
        await interaction.response.send_message("❌ Value must be a non-negative number.", ephemeral=True)
        return
    await db.set_bot_setting(key, str(num))
    embed = discord.Embed(
        title="⚙️ Config Updated",
        color=discord.Color.gold(),
        description=f"**{key}** set to **{value}**\n_{VALID_CONFIG_KEYS[key]}_",
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="config", description="[Admin] View all current economy settings.")
async def config_cmd(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    settings = await db.get_all_settings()
    lines = []
    for k, desc in VALID_CONFIG_KEYS.items():
        val = settings.get(k, "?")
        lines.append(f"• **{k}** = `{val}` — {desc}")
    embed = discord.Embed(
        title="⚙️ Economy Config",
        color=discord.Color.blurple(),
        description="\n".join(lines),
    )
    embed.set_footer(text="Use /setconfig <key> <value> to change any setting")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def shop_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    items = await db.get_shop_items()
    embed = discord.Embed(title="🛒 Duck Exchange Shop", color=discord.Color.gold())
    if not items:
        embed.description = "The shop is empty! An admin can add items with `/createitem`."
    else:
        lines = []
        for item in items:
            stock_str = "∞" if item["stock"] == -1 else str(item["stock"])
            lines.append(
                f"**[#{item['id']}] {item['name']}** — {fmt_money(item['price'])}  _(stock: {stock_str})_\n"
                f"  _{item['description']}_"
            )
        embed.description = "\n\n".join(lines)
        embed.set_footer(text="Use /buyitem <id> to purchase")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="buyitem", description="Buy an item from the admin shop.")
@app_commands.describe(item_id="Item ID from /shop")
async def buyitem_cmd(interaction: discord.Interaction, item_id: int):
    await ensure(interaction)
    result = await db.buy_shop_item(str(interaction.user.id), item_id)
    if result == "not_found":
        await interaction.response.send_message(f"❌ No item with ID **#{item_id}** found.", ephemeral=True)
        return
    if result == "out_of_stock":
        await interaction.response.send_message("❌ That item is out of stock.", ephemeral=True)
        return
    if result == "insufficient_funds":
        item = await db.get_shop_item(item_id)
        user = await db.get_user(str(interaction.user.id))
        await interaction.response.send_message(
            f"❌ You need {fmt_money(item['price'])} but only have {fmt_money(user['cash'])} in your wallet.",
            ephemeral=True,
        )
        return
    item_info = result
    if item_info["role_id"]:
        role = interaction.guild.get_role(int(item_info["role_id"])) if interaction.guild else None
        if role:
            try:
                await interaction.user.add_roles(role, reason="Purchased from shop")
                embed = discord.Embed(
                    title="✅ Purchase Successful",
                    color=discord.Color.green(),
                    description=(
                        f"You bought **{item_info['name']}** and received the **{role.name}** role!\n"
                        f"_{item_info['description']}_"
                    ),
                )
            except discord.Forbidden:
                embed = discord.Embed(
                    title="⚠️ Purchase Successful (Role Error)",
                    color=discord.Color.orange(),
                    description=(
                        f"You bought **{item_info['name']}** but the bot couldn't assign the role.\n"
                        f"Please contact an admin."
                    ),
                )
        else:
            embed = discord.Embed(
                title="⚠️ Purchase Successful (Role Not Found)",
                color=discord.Color.orange(),
                description=f"You bought **{item_info['name']}** but the linked role no longer exists. Contact an admin.",
            )
    else:
        embed = discord.Embed(
            title="✅ Purchase Successful",
            color=discord.Color.green(),
            description="You bought an item! Check `/inventory` to see it.",
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="createitem", description="[Admin] Add an item to the shop.")
@app_commands.describe(
    name="Item name",
    price="Price in cash",
    description="Item description",
    stock="Stock amount (-1 = unlimited)",
    role_id="Discord Role ID to grant on purchase (optional, replaces inventory reward)",
)
async def createitem_cmd(
    interaction: discord.Interaction,
    name: str,
    price: float,
    description: str = "",
    stock: int = -1,
    role_id: str = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if price <= 0:
        await interaction.response.send_message("❌ Price must be positive.", ephemeral=True)
        return
    if role_id is not None:
        if not role_id.isdigit():
            await interaction.response.send_message("❌ role_id must be a numeric Discord Role ID.", ephemeral=True)
            return
        if interaction.guild and not interaction.guild.get_role(int(role_id)):
            await interaction.response.send_message(f"❌ No role with ID {role_id} found in this server.", ephemeral=True)
            return
    item_id = await db.create_shop_item(name, description, price, stock, role_id)
    stock_str = "Unlimited" if stock == -1 else str(stock)
    role_str = f"  ·  🎭 Grants role <@&{role_id}>" if role_id else ""
    embed = discord.Embed(
        title="✅ Item Created",
        color=discord.Color.green(),
        description=f"**[#{item_id}] {name}**\n{description}\n\nPrice: {fmt_money(price)}  |  Stock: {stock_str}{role_str}",
    )
    await interaction.response.send_message(embed=embed)


@app_commands.describe(
    item_id="Item ID to edit",
    name="New name (leave blank to keep)",
    price="New price (leave blank to keep)",
    description="New description (leave blank to keep)",
    stock="New stock amount (-1 = unlimited, leave blank to keep)",
)
async def edititem_cmd(
    interaction: discord.Interaction,
    item_id: int,
    name: str = None,
    price: float = None,
    description: str = None,
    stock: int = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    updates = {}
    if name is not None:
        updates["name"] = name
    if price is not None:
        if price <= 0:
            await interaction.response.send_message("❌ Price must be positive.", ephemeral=True)
            return
        updates["price"] = price
    if description is not None:
        updates["description"] = description
    if stock is not None:
        updates["stock"] = stock
    if not updates:
        await interaction.response.send_message("❌ Provide at least one field to update.", ephemeral=True)
        return
    ok = await db.edit_shop_item(item_id, **updates)
    if not ok:
        await interaction.response.send_message(f"❌ No item with ID **#{item_id}** found.", ephemeral=True)
        return
    item = await db.get_shop_item(item_id)
    stock_str = "Unlimited" if item["stock"] == -1 else str(item["stock"])
    embed = discord.Embed(
        title="✏️ Item Updated",
        color=discord.Color.gold(),
        description=(
            f"**[#{item['id']}] {item['name']}**\n"
            f"{item['description']}\n\n"
            f"Price: {fmt_money(item['price'])}  |  Stock: {stock_str}"
        ),
    )
    changed = ", ".join(updates.keys())
    embed.set_footer(text=f"Updated: {changed}")
    await interaction.response.send_message(embed=embed)


# ── /market ───────────────────────────────────────────────────────────────────

@bot.tree.command(name="market", description="Browse the community marketplace.")

async def market_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    listings = await db.get_listings()
    embed = discord.Embed(title="🏪 Community Market", color=discord.Color.blurple())
    if not listings:
        embed.description = "The market is empty! Use `/listitem` to sell something."
    else:
        lines = []
        for l in listings:
            lines.append(
                f"**[#{l['id']}] {l['name']}** — {fmt_money(l['price'])}  _(by {l['seller_name']})_\n"
                f"  _{l['description']}_"
            )
        embed.description = "\n\n".join(lines)
        embed.set_footer(text="Use /buymarket <id> to buy  ·  /delistitem <id> to remove your listing")
    await interaction.response.send_message(embed=embed)
    
async def inventory_autocomplete(
    interaction: discord.Interaction,
    current: str,
):
    items = await db.get_user_items(str(interaction.user.id))

    unique_items = []
    seen = set()

    for item in items:
        name = item["item_name"]

        if name in seen:
            continue

        seen.add(name)

        if current.lower() in name.lower():
            unique_items.append(
                app_commands.Choice(
                    name=name,
                    value=name
                )
            )

    return unique_items[:25]

@bot.tree.command(name="listitem", description="List one of your inventory items on the community market.")
@app_commands.describe(
    item_name="Item from your inventory",
    price="Price you want"
)
@app_commands.autocomplete(item_name=inventory_autocomplete)
async def listitem_cmd(
    interaction: discord.Interaction,
    item_name: str,
    price: float,
):
    await ensure(interaction)

    if price <= 0:
        await interaction.response.send_message(
            "❌ Price must be positive.",
            ephemeral=True
        )
        return

    items = await db.get_user_items(str(interaction.user.id))

    owned_item = None

    for item in items:
        if item["item_name"] == item_name:
            owned_item = item
            break

    if owned_item is None:
        await interaction.response.send_message(
            "❌ You do not own that item.",
            ephemeral=True
        )
        return

    await db.remove_user_item(
        str(interaction.user.id),
        item_name
    )

    listing_id = await db.create_listing(
        str(interaction.user.id),
        interaction.user.display_name,
        owned_item["item_name"],
        owned_item["item_description"],
        price
    )

    embed = discord.Embed(
        title="📦 Item Listed!",
        color=discord.Color.green(),
        description=(
            f"**{owned_item['item_name']}** listed for "
            f"**{fmt_money(price)}**\n"
            f"Listing ID: **#{listing_id}**"
        ),
    )

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="buymarket", description="Buy an item from the community market.")
@app_commands.describe(listing_id="Listing ID from /market")
async def buymarket_cmd(interaction: discord.Interaction, listing_id: int):
    await ensure(interaction)
    result = await db.buy_listing(str(interaction.user.id), listing_id)
    if result == "not_found":
        await interaction.response.send_message(f"❌ No listing **#{listing_id}** found — it may have already sold.", ephemeral=True)
        return
    if result == "own_listing":
        await interaction.response.send_message("❌ You can't buy your own listing!", ephemeral=True)
        return
    if result == "insufficient_funds":
        await interaction.response.send_message("❌ Not enough wallet cash.", ephemeral=True)
        return
    embed = discord.Embed(
        title="✅ Purchase Successful!",
        color=discord.Color.green(),
        description=f"Item purchased from the community market! Check `/inventory` to see it.",
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="delistitem", description="Remove your listing from the community market.")
@app_commands.describe(listing_id="Listing ID to remove")
async def delistitem_cmd(interaction: discord.Interaction, listing_id: int):
    await ensure(interaction)
    result = await db.delist_item(str(interaction.user.id), listing_id)
    if result == "not_found":
        await interaction.response.send_message(f"❌ No listing **#{listing_id}** found.", ephemeral=True)
        return
    if result == "not_yours":
        await interaction.response.send_message("❌ That's not your listing.", ephemeral=True)
        return
    await interaction.response.send_message(f"🗑️ Listing **#{listing_id}** removed from the market.", ephemeral=True)


# ── /inventory ────────────────────────────────────────────────────────────────

@bot.tree.command(name="inventory", description="View all items you own.")

async def inventory_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    items = await db.get_user_items(str(interaction.user.id))
    embed = discord.Embed(
        title=f"🎒 {interaction.user.display_name}'s Inventory",
        color=discord.Color.og_blurple(),
    )
    if not items:
        embed.description = "Your inventory is empty! Visit `/shop` or `/market` to buy something."
    else:
        lines = []
        for it in items:
            source_icon = "🛒" if it["source"] == "shop" else "🏪"
            desc = f" — _{it['item_description']}_" if it["item_description"] else ""
            lines.append(f"{source_icon} **{it['item_name']}**{desc}")
        embed.description = "\n".join(lines)
        embed.set_footer(text=f"{len(items)} item(s)  ·  🛒 = admin shop  ·  🏪 = market")
    await interaction.response.send_message(embed=embed)




# ── /help ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="help", description="Show all Duck Exchange commands.")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🦆 Duck Exchange — Command List",
        color=discord.Color.yellow(),
        description="All commands work with both `/` (slash) and `?` (prefix).",
    )
    embed.add_field(name="💰 Economy", inline=False, value=(
        "`?balance` / `?bal` — Check your wallet & bank\n"
        "`?deposit <amt|all>` / `?dep` — Move wallet → bank\n"
        "`?withdraw <amt|all>` / `?with` / `?wd` — Move bank → wallet\n"
        "`?portfolio` / `?port` / `?pf` — Full net worth & holdings\n"
        "`?leaderboard` / `?lb` — Top 10 richest players\n"
        "`?transfer @user <amt|all>` / `?tr` — Send wallet cash\n"
    ))
    embed.add_field(name="📈 Stocks", inline=False, value=(
        "`?stocks` / `?st` — List all stocks & prices\n"
        "`?stock <TICKER>` — Detailed info on one stock\n"
        "`?buy <TICKER> <amt|all>` — Buy shares (all = spend all cash)\n"
        "`?sell <TICKER> <amt|all>` — Sell shares (all = sell everything)\n"
        "`?chart <TICKER>` / `?ch` — Price history chart\n"
        "`?marketsummary` / `?ms` — Top movers (24h)\n"
    ))
    embed.add_field(name="🎮 Games & Earning", inline=False, value=(
        "`?work` — Earn cash (3 min cooldown)\n"
        "`?crime` — Risky cash grab (5 min cooldown)\n"
        "`?claim` / `?daily` — Claim periodic bonus\n"
        "`?flip <heads|tails> <amt|all>` — 50/50 coin flip\n"
        "`?blackjack <amt|all>` / `?bj` — Play blackjack\n"
        "`?roulette <bet> <amt|all>` / `?rou` — Spin the wheel\n"
        "`?steal @user` / `?rob` — Attempt to steal a player's wallet\n"
    ))
    embed.add_field(name="🎒 Items", inline=False, value=(
        "`?inventory` / `?inv` — View your items\n"
        "`?market` — Browse community listings\n"
    ))
    embed.add_field(name="🔧 Admin Only", inline=False, value=(
        "`/givecash @user <amt>` — Give wallet cash\n"
        "`/givebank @user <amt>` / `?gb` — Give bank cash\n"
        "`/removecash @user <amt>` — Remove cash\n"
        "`/createstock` / `?cs TICKER,Name` — Create a stock\n"
        "`/editstock` · `/deletestock` · `/setconfig`\n"
    ))
    embed.set_footer(text="For numbers: use 'all' to mean your full balance  ·  Partial ?withdraw always works")
    await interaction.response.send_message(embed=embed)


# ── ?help ────────────────────────────────────────────────────────────────────

@bot.command(name="cmds", aliases=["h", "commands"])
async def prefix_help(ctx):
    embed = discord.Embed(
        title="🦆 Duck Exchange — Command List",
        color=discord.Color.yellow(),
        description="All commands work with both `/` (slash) and `?` (prefix).",
    )
    embed.add_field(name="💰 Economy", inline=False, value=(
        "`?balance` / `?bal` — Check your wallet & bank\n"
        "`?deposit <amt|all>` / `?dep` — Move wallet → bank\n"
        "`?withdraw <amt|all>` / `?with` / `?wd` — Move bank → wallet\n"
        "`?portfolio` / `?port` / `?pf` — Full net worth & holdings\n"
        "`?leaderboard` / `?lb` — Top 10 richest players\n"
        "`?transfer @user <amt|all>` / `?tr` — Send wallet cash\n"
    ))
    embed.add_field(name="📈 Stocks", inline=False, value=(
        "`?stocks` / `?st` — List all stocks & prices\n"
        "`?stock <TICKER>` — Detailed info on one stock\n"
        "`?buy <TICKER> <amt|all>` — Buy shares (all = spend all cash)\n"
        "`?sell <TICKER> <amt|all>` — Sell shares (all = sell everything)\n"
        "`?chart <TICKER>` / `?ch` — Price history chart\n"
        "`?marketsummary` / `?ms` — Top movers (24h)\n"
    ))
    embed.add_field(name="🎮 Games & Earning", inline=False, value=(
        "`?work` — Earn cash (3 min cooldown)\n"
        "`?crime` — Risky cash grab (5 min cooldown)\n"
        "`?claim` / `?daily` — Claim periodic bonus\n"
        "`?flip <heads|tails> <amt|all>` — 50/50 coin flip\n"
        "`?blackjack <amt|all>` / `?bj` — Play blackjack\n"
        "`?roulette <bet> <amt|all>` / `?rou` — Spin the wheel\n"
        "`?steal @user` / `?rob` — Attempt to steal a player's wallet\n"
    ))
    embed.add_field(name="🎒 Items", inline=False, value=(
        "`?inventory` / `?inv` — View your items\n"
        "`?market` — Browse community listings\n"
    ))
    embed.add_field(name="🔧 Admin Only", inline=False, value=(
        "`/givecash @user <amt>` — Give wallet cash\n"
        "`?givebank @user <amt>` / `?gb` — Give bank cash\n"
        "`/removecash @user <amt>` — Remove cash\n"
        "`/createstock` / `?cs TICKER,Name` — Create a stock\n"
        "`/editstock` · `/deletestock` · `/setconfig`\n"
    ))
    embed.set_footer(text="For numbers: use 'all' to mean your full balance  ·  Partial ?withdraw always works")
    await ctx.send(embed=embed)

# ── ?cs quick-create stock ──────────────────────────────────────────────────

@bot.command(name="cs")
async def quick_create_stock(ctx, *, args: str = ""):
    """Quick-create a stock: ?cs TICKER,Full Name"""
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Only server administrators can use this command.", delete_after=8)
        return
    parts = args.split(",", 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        await ctx.send(
            "❌ Usage: `?cs TICKER,Full Name`\n"
            "Example: `?cs ARSE,Australian Research and Space Exploration`",
            delete_after=15,
        )
        return
    ticker = parts[0].strip().upper()
    name = parts[1].strip()
    if not ticker.isalnum() or len(ticker) > 10:
        await ctx.send("❌ Ticker must be letters/numbers only, max 10 characters.", delete_after=8)
        return
    existing = await db.get_stock(ticker)
    if existing:
        await ctx.send(f"❌ Stock **{ticker}** already exists.", delete_after=8)
        return
    await db.create_stock(
        ticker=ticker,
        name=name,
        price=2500.0,
        min_change=0.0,
        max_change=500.0,
        fluctuation_minutes=1.5,
    )
    embed = discord.Embed(
        title=f"✅ Stock Created: {ticker}",
        color=discord.Color.green(),
        description=(
            f"**{name}** (`{ticker}`)\n"
            f"💵 Starting price: **$2,500.00**\n"
            f"📊 Change range: **$0 – $500** per tick\n"
            f"⏱ Updates every: **1.5 minutes**"
        ),
    )
    embed.set_footer(text="Use /editstock to adjust settings later.")
    await ctx.send(embed=embed)


@bot.command(name="sync")
async def prefix_sync(ctx):
    """[Admin] Force-sync slash commands with Discord so new params appear immediately."""
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Only server administrators can use this command.", delete_after=8)
        return
    msg = await ctx.send("🔄 Syncing slash commands with Discord...")
    synced = await bot.tree.sync()
    await msg.edit(content=f"✅ Synced **{len(synced)}** slash commands with Discord. New params should appear now.")


# ═══════════════════════════════════════════════════════════════════════════════
# PREFIX COMMANDS  (? and ! prefix — mirrors all slash commands)
# ═══════════════════════════════════════════════════════════════════════════════

async def _resolve_amount(user_id: str, raw: str, source: str = "cash") -> tuple:
    """Parse a raw amount string ('all' or a number). Returns (amount, error_str)."""
    if raw.lower() == "all":
        user = await db.get_user(user_id)
        if user is None:
            return None, "❌ User not found."
        amount = user[source]
        if amount <= 0:
            label = "wallet" if source == "cash" else "bank"
            return None, f"❌ Your {label} is empty."
        return amount, None
    try:
        amount = float(raw)
        if amount <= 0:
            return None, "❌ Amount must be positive."
        return amount, None
    except ValueError:
        return None, "❌ Invalid amount. Use a number or `all`."


# ── ?balance ──────────────────────────────────────────────────────────────────

@bot.command(name="balance", aliases=["bal", "wallet"])
async def prefix_balance(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    user = await db.get_user(str(ctx.author.id))
    embed = discord.Embed(
        title=f"💰 {ctx.author.display_name}'s Balance",
        color=discord.Color.green(),
    )
    embed.add_field(name="👛 Wallet", value=fmt_money(user["cash"]), inline=True)
    embed.add_field(name="🏦 Bank", value=fmt_money(user["bank"]), inline=True)
    embed.add_field(name="💰 Total", value=fmt_money(user["cash"] + user["bank"]), inline=True)
    if user.get("loan_amount") and user["loan_amount"] > 0:
        from datetime import datetime, timezone
        due = datetime.fromisoformat(user["loan_due"]) if user.get("loan_due") else None
        time_left = int((due - datetime.now(timezone.utc)).total_seconds() / 60) if due else 0
        due_str = f"{max(time_left, 0)} min" if time_left > 0 else "\u26a0\ufe0f OVERDUE"
        embed.add_field(name="\U0001f4b8 Loan Owed", value=f"{fmt_money(user['loan_amount'])} (due in {due_str})", inline=False)
        embed.color = discord.Color.red()
    embed.set_footer(text="Use ?deposit or ?withdraw to move money · Wallet is stealable, bank is safe!")
    await ctx.send(embed=embed)


# ── ?deposit ──────────────────────────────────────────────────────────────────

@bot.command(name="deposit", aliases=["dep"])
async def prefix_deposit(ctx, amount_str: str = ""):
    if not amount_str:
        await ctx.send("❌ Usage: `?deposit <amount>` or `?deposit all`")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    amount, err = await _resolve_amount(str(ctx.author.id), amount_str, "cash")
    if err:
        await ctx.send(err)
        return
    result = await db.deposit(str(ctx.author.id), amount)
    if result == "insufficient_funds":
        user = await db.get_user(str(ctx.author.id))
        await ctx.send(f"❌ You only have {fmt_money(user['cash'])} in your wallet.")
        return
    user = await db.get_user(str(ctx.author.id))
    embed = discord.Embed(
        title="🏦 Deposit Successful",
        color=discord.Color.green(),
        description=(
            f"Deposited **{fmt_money(amount)}** into your bank.\n"
            f"👛 Wallet: {fmt_money(user['cash'])}  |  🏦 Bank: {fmt_money(user['bank'])}"
        ),
    )
    await ctx.send(embed=embed)


# ── ?withdraw ─────────────────────────────────────────────────────────────────

@bot.command(name="withdraw", aliases=["with", "wd"])
async def prefix_withdraw(ctx, amount_str: str = ""):
    if not amount_str:
        await ctx.send("❌ Usage: `?withdraw <amount>` or `?withdraw all`")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    user = await db.get_user(str(ctx.author.id))
    if amount_str.lower() == "all":
        actual = user["bank"]
    else:
        try:
            requested = float(amount_str)
        except ValueError:
            await ctx.send("❌ Invalid amount. Use a number or `all`.")
            return
        actual = min(requested, user["bank"])
    if actual <= 0:
        await ctx.send("❌ Your bank is empty.")
        return
    await db.withdraw(str(ctx.author.id), actual)
    user = await db.get_user(str(ctx.author.id))
    desc = f"Withdrew **{fmt_money(actual)}** from your bank.\n"
    if amount_str.lower() != "all":
        requested = float(amount_str)
        if actual < requested:
            desc += f"_(You only had {fmt_money(actual)}, so that amount was withdrawn.)_\n"
    desc += f"👛 Wallet: {fmt_money(user['cash'])}  |  🏦 Bank: {fmt_money(user['bank'])}"
    embed = discord.Embed(title="🏦 Withdrawal Successful", color=discord.Color.green(), description=desc)
    await ctx.send(embed=embed)


# ── ?portfolio ────────────────────────────────────────────────────────────────

@bot.command(name="portfolio", aliases=["pf", "holdings", "port"])
async def prefix_portfolio(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    user = await db.get_user(str(ctx.author.id))
    holdings = await db.get_user_holdings(str(ctx.author.id))
    cash = user["cash"]
    bank = user["bank"]
    holdings_value = sum(h["shares"] * h["price"] for h in holdings)
    net_worth = cash + bank + holdings_value
    embed = discord.Embed(title=f"🦆 {ctx.author.display_name}'s Portfolio", color=discord.Color.green())
    embed.add_field(name="👛 Wallet", value=fmt_money(cash), inline=True)
    embed.add_field(name="🏦 Bank", value=fmt_money(bank), inline=True)
    embed.add_field(name="📈 Holdings Value", value=fmt_money(holdings_value), inline=True)
    embed.add_field(name="🏆 Net Worth", value=fmt_money(net_worth), inline=True)
    if holdings:
        lines = []
        for h in holdings:
            current_value = h["shares"] * h["price"]
            cost_basis = h["shares"] * h["avg_cost"]
            pl = current_value - cost_basis
            pl_pct = (pl / cost_basis * 100) if cost_basis > 0 else 0.0
            pl_sign = "+" if pl >= 0 else ""
            pl_emoji = "📈" if pl >= 0 else "📉"
            lines.append(
                f"**{h['ticker']}** ({h['name']}) — {h['shares']:,} shares\n"
                f"  Bought @ {fmt_money(h['avg_cost'])} avg  ·  Now {fmt_money(h['price'])}\n"
                f"  Value: {fmt_money(current_value)}  ·  P&L: {pl_emoji} {pl_sign}{fmt_money(pl)} ({pl_sign}{pl_pct:.1f}%)"
            )
        embed.add_field(name="📊 Shares Owned", value="\n\n".join(lines), inline=False)
    else:
        embed.add_field(name="📊 Shares Owned", value="None — use `?buy` to get started!", inline=False)
    await ctx.send(embed=embed)


# ── ?leaderboard ──────────────────────────────────────────────────────────────

@bot.command(name="leaderboard", aliases=["lb", "top"])
async def prefix_leaderboard(ctx):
    rows = await db.get_leaderboard(10)
    if not rows:
        await ctx.send("No users yet! Use `?portfolio` to register.")
        return
    embed = discord.Embed(title="🏆 Duck Exchange Leaderboard", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        net_worth = row["cash"] + row["bank"] + row["holdings_value"]
        medal = medals[i] if i < 3 else f"**#{i+1}**"
        lines.append(f"{medal} **{row['username']}** — {fmt_money(net_worth)}")
    embed.description = "\n".join(lines)
    await ctx.send(embed=embed)


# ── ?stocks ───────────────────────────────────────────────────────────────────

@bot.command(name="stocks", aliases=["st"])
async def prefix_stocks(ctx):
    rows = await db.get_all_stocks()
    if not rows:
        await ctx.send("No stocks created yet. An admin can use `?cs` to add one.")
        return
    embed = discord.Embed(title="🦆 Duck Exchange — Stock Market", color=discord.Color.yellow())
    lines = [f"**{r['ticker']}** — {r['name']} — {fmt_money(r['price'])}" for r in rows]
    embed.description = "\n".join(lines)
    embed.set_footer(text="Use ?buy <ticker> <amount> to invest!")
    await ctx.send(embed=embed)


# ── ?stock <ticker> ───────────────────────────────────────────────────────────

@bot.command(name="stock")
async def prefix_stock(ctx, ticker: str = ""):
    if not ticker:
        await ctx.send("❌ Usage: `?stock <TICKER>`")
        return
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await ctx.send(f"❌ No stock **{ticker}** found.")
        return
    owners = await db.get_owners_of_stock(ticker)
    changes = await db.get_recent_price_changes(ticker, 5)
    fee_pct = await db.get_bot_setting("transaction_fee_pct", "2")
    if changes:
        net = sum(changes)
        trend = "📈 Trending Up" if net > 0 else ("📉 Trending Down" if net < 0 else "➡️ Flat")
    else:
        trend = "❓ No data yet"
    base_p = stock["base_price"] if stock["base_price"] > 0 else stock["price"]
    vs_base = stock["price"] - base_p
    vs_base_pct = (vs_base / base_p * 100) if base_p != 0 else 0.0
    vs_sign = "+" if vs_base >= 0 else ""
    floor = round(base_p * 0.05, 2)
    change_str = "  ".join(
        ("🟡 +$0.00" if c == 0 else ("🟢 +" + fmt_money(c) if c > 0 else "🔴 " + fmt_money(c)))
        for c in changes
    ) if changes else "No history"
    shares_held = await db.get_shares_held(ticker)
    max_s = stock["max_shares"] if stock["max_shares"] else 50
    remaining = max_s - shares_held

    embed = discord.Embed(title=f"📊 {ticker} — {stock['name']}", color=discord.Color.yellow())
    embed.add_field(name="💵 Current Price", value=fmt_money(stock["price"]), inline=True)
    embed.add_field(name="🏁 Base Price", value=fmt_money(base_p), inline=True)
    embed.add_field(name="📐 vs Base", value=f"{vs_sign}{fmt_money(vs_base)} ({vs_sign}{vs_base_pct:.1f}%)", inline=True)
    embed.add_field(name="🛡️ Price Floor", value=fmt_money(floor), inline=True)
    embed.add_field(name="👥 Shareholders", value=str(len(owners)), inline=True)
    embed.add_field(name="📉 Trend", value=trend, inline=True)
    embed.add_field(name="📦 Supply", value=f"{remaining:,}/{max_s:,} shares left", inline=True)
    embed.add_field(
        name="⚙️ Volatility",
        value=f"Change range: ${stock['min_change']:,.0f}–${stock['max_change']:,.0f}\nInterval: every **{stock['fluctuation_minutes']}** min",
        inline=False,
    )
    embed.add_field(name="🕐 Last 5 Changes", value=change_str, inline=False)
    embed.set_footer(text=f"Transaction fee: {fee_pct}% on buy & sell  ·  Use ?chart for price history")
    await ctx.send(embed=embed)


# ── ?buy <ticker> <amount|all> ────────────────────────────────────────────────

@bot.command(name="buy")
async def prefix_buy(ctx, ticker: str = "", amount_str: str = ""):
    if not ticker or not amount_str:
        await ctx.send("❌ Usage: `?buy <TICKER> <amount>` or `?buy <TICKER> all`")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await ctx.send(f"❌ No stock **{ticker}** found. Use `?stocks` to see available stocks.")
        return
    if amount_str.lower() == "all":
        fee_pct_val = float(await db.get_bot_setting("transaction_fee_pct", "2"))
        user = await db.get_user(str(ctx.author.id))
        max_shares = int(user["cash"] / (stock["price"] * (1 + fee_pct_val / 100)))
        if max_shares <= 0:
            await ctx.send(f"❌ You don't have enough cash to buy any shares of **{ticker}**.")
            return
        amount = max_shares
    else:
        try:
            amount = int(float(amount_str))
        except ValueError:
            await ctx.send("❌ Invalid amount. Use a whole number or `all`.")
            return
    if amount <= 0:
        await ctx.send("❌ Amount must be at least 1.")
        return
    result = await db.buy_stock(str(ctx.author.id), ticker, amount, stock["price"])
    if result == "too_many_shares":
        holdings = await db.get_user_holdings(str(ctx.author.id))
        owned = sum(h["shares"] for h in holdings if h["ticker"] == ticker)
        cap = stock["max_shares"]
        await ctx.send(f"❌ **{ticker}** supply cap is **{cap:,}** shares — none left to buy.\nYou own: **{owned:,}/{cap:,}**  ·  Trying to buy: **{amount:,}**")
        return
    if result == "insufficient_funds":
        fee_pct = await db.get_bot_setting("transaction_fee_pct", "2")
        await ctx.send(f"❌ You don't have enough cash.\n_(Note: a **{fee_pct}% transaction fee** is added to the total cost)_")
        return
    new_price, fee = result
    user = await db.get_user(str(ctx.author.id))
    fee_pct_show = await db.get_bot_setting("transaction_fee_pct", "2")
    total_cost = amount * stock["price"]
    embed = discord.Embed(
        title="✅ Purchase Successful",
        color=discord.Color.green(),
        description=(
            f"You bought **{amount:,} shares** of **{ticker}** ({stock['name']}) at {fmt_money(stock['price'])} each.\n"
            f"**Subtotal:** {fmt_money(total_cost)}\n"
            f"**Transaction fee ({fee_pct_show}%):** {fmt_money(fee)}\n"
            f"**Total cost:** {fmt_money(total_cost + fee)}\n"
            f"**New wallet balance:** {fmt_money(user['cash'])}"
        ),
    )
    await ctx.send(embed=embed)


# ── ?sell <ticker> <amount|all> ───────────────────────────────────────────────

@bot.command(name="sell")
async def prefix_sell(ctx, ticker: str = "", amount_str: str = ""):
    if not ticker or not amount_str:
        await ctx.send("❌ Usage: `?sell <TICKER> <amount>` or `?sell <TICKER> all`")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await ctx.send(f"❌ No stock **{ticker}** found.")
        return
    holdings = await db.get_user_holdings(str(ctx.author.id))
    holding = next((h for h in holdings if h["ticker"] == ticker), None)
    if not holding or holding["shares"] <= 0:
        await ctx.send(f"❌ You don't own any shares of **{ticker}**.")
        return
    if amount_str.lower() == "all":
        amount = holding["shares"]
    else:
        try:
            amount = int(float(amount_str))
        except ValueError:
            await ctx.send("❌ Invalid amount. Use a whole number or `all`.")
            return
    if amount <= 0:
        await ctx.send("❌ Amount must be at least 1.")
        return
    result = await db.sell_stock(str(ctx.author.id), ticker, amount, stock["price"])
    if result == "insufficient_shares":
        await ctx.send(f"❌ You only own **{holding['shares']} shares** of **{ticker}**.")
        return
    new_price, fee = result
    fee_pct_val = await db.get_bot_setting("transaction_fee_pct", "2")
    gross = amount * stock["price"]
    net_proceeds = gross - fee
    user = await db.get_user(str(ctx.author.id))
    embed = discord.Embed(
        title="✅ Sale Successful",
        color=discord.Color.green(),
        description=(
            f"You sold **{amount:,} shares** of **{ticker}** ({stock['name']}) at {fmt_money(stock['price'])} each.\n"
            f"**Gross proceeds:** {fmt_money(gross)}\n"
            f"**Transaction fee ({fee_pct_val}% per share):** -{fmt_money(fee)}\n"
            f"**Net proceeds:** {fmt_money(net_proceeds)}\n"
            f"**New cash balance:** {fmt_money(user['cash'])}\n"
            f"**New market price:** 📉 {fmt_money(new_price)}"
        ),
    )
    await ctx.send(embed=embed)


# ── ?chart <ticker> ───────────────────────────────────────────────────────────

@bot.command(name="chart", aliases=["ch"])
async def prefix_chart(ctx, ticker: str = ""):
    if not ticker:
        await ctx.send("❌ Usage: `?chart <TICKER>`")
        return
    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await ctx.send(f"❌ No stock **{ticker}** found.")
        return
    history = await db.get_price_history(ticker)
    if not history or len(history) < 2:
        await ctx.send(f"📊 **{ticker}** — {stock['name']}\nCurrent price: {fmt_money(stock['price'])}\n\nNot enough price history yet.")
        return
    prices = [row["price"] for row in history]
    owners = await db.get_owners_of_stock(ticker)
    base_price = stock["base_price"] if stock["base_price"] > 0 else prices[0]
    buf = render_chart_image(prices, ticker, stock["name"], shareholders=len(owners), base_price=base_price)
    await ctx.send(file=discord.File(buf, filename=f"{ticker}_chart.png"))


# ── ?work ─────────────────────────────────────────────────────────────────────

@bot.command(name="work")
async def prefix_work(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    result = await db.do_work(str(ctx.author.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        await ctx.send(f"⏳ You're too tired to work! Rest for **{m}m {s}s**.")
        return
    work_min = await db.get_bot_setting("work_min", "50")
    work_max = await db.get_bot_setting("work_max", "200")
    msg = random.choice(WORK_MESSAGES)
    embed = discord.Embed(
        title="💼 Work Complete",
        color=discord.Color.blue(),
        description=(
            f"{msg} and earned **{fmt_money(result['earned'])}**!\n\n"
            f"👛 New wallet: {fmt_money(result['new_cash'])}"
        ),
    )
    embed.set_footer(text=f"Cooldown: 3 min  ·  Payout: ${work_min}–${work_max}")
    await ctx.send(embed=embed)


# ── ?crime ────────────────────────────────────────────────────────────────────

@bot.command(name="crime")
async def prefix_crime(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    result = await db.do_crime(str(ctx.author.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        await ctx.send(f"⏳ Laying low after last time. Try again in **{m}m {s}s**.")
        return
    crime_min = await db.get_bot_setting("crime_min", "100")
    crime_max = await db.get_bot_setting("crime_max", "500")
    crime_fail_pct = await db.get_bot_setting("crime_fail_pct", "30")
    if result["success"]:
        msg = random.choice(CRIME_SUCCESS_MESSAGES)
        embed = discord.Embed(
            title="🦹 Crime Successful",
            color=discord.Color.green(),
            description=f"{msg} and pocketed **{fmt_money(result['earned'])}**!\n\n👛 New wallet: {fmt_money(result['new_cash'])}",
        )
    else:
        msg = random.choice(CRIME_FAIL_MESSAGES)
        embed = discord.Embed(
            title="🚔 Busted!",
            color=discord.Color.red(),
            description=(
                f"{msg}\n\n💸 You lost **{fmt_money(result['penalty'])}** ({crime_fail_pct}% of your wealth) in fines!\n"
                f"👛 Wallet: {fmt_money(result['new_cash'])}  |  🏦 Bank: {fmt_money(result['new_bank'])}"
            ),
        )
    embed.set_footer(text=f"Cooldown: 5 min  ·  Payout: ${crime_min}–${crime_max}  ·  Fail: {crime_fail_pct}% wealth")
    await ctx.send(embed=embed)


# ── ?claim ────────────────────────────────────────────────────────────────────

@bot.command(name="claim", aliases=["daily"])
async def prefix_claim(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    result = await db.claim_daily(str(ctx.author.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        time_str = f"{m}m {s}s" if m else f"{s}s"
        await ctx.send(f"⏳ Already claimed! Come back in **{time_str}**.")
        return
    reward = result.get("reward", 100)
    cooldown = result.get("cooldown_secs", 60)
    c_m, c_s = divmod(cooldown, 60)
    cooldown_str = f"{c_m}m {c_s}s" if c_m else f"{c_s}s"
    embed = discord.Embed(
        title=f"✅ Claimed {fmt_money(reward)}!",
        color=discord.Color.green(),
        description=f"💵 Added **{fmt_money(reward)}** to your wallet.\n👛 New wallet balance: {fmt_money(result['new_cash'])}",
    )
    embed.set_footer(text=f"You can claim again in {cooldown_str}!")
    await ctx.send(embed=embed)


# ── ?flip <heads|tails> <amount|all> ─────────────────────────────────────────

@bot.command(name="flip")
async def prefix_flip(ctx, choice: str = "", amount_str: str = ""):
    if not choice or not amount_str:
        await ctx.send("❌ Usage: `?flip <heads|tails> <amount>` or `?flip heads all`")
        return
    choice = choice.lower()
    if choice not in ("heads", "tails"):
        await ctx.send("❌ Choose `heads` or `tails`.")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    amount, err = await _resolve_amount(str(ctx.author.id), amount_str, "cash")
    if err:
        await ctx.send(err)
        return
    user = await db.get_user(str(ctx.author.id))
    if user["cash"] < amount:
        await ctx.send(f"❌ You only have {fmt_money(user['cash'])} in your wallet.")
        return
    flip = random.choice(["heads", "tails"])
    won = flip == choice
    net = amount if won else -amount
    async with aiosqlite.connect(db.DB_PATH) as _db:
        await _db.execute("UPDATE users SET cash = cash + ? WHERE user_id = ?", (net, str(ctx.author.id)))
        await _db.commit()
    user_after = await db.get_user(str(ctx.author.id))
    coin = "🟡 Heads" if flip == "heads" else "⚫ Tails"
    embed = discord.Embed(
        title=f"{coin}!",
        color=discord.Color.green() if won else discord.Color.red(),
        description=(
            f"You picked **{choice}** and the coin landed on **{flip}**.\n"
            f"{'🎉 You won' if won else '💸 You lost'} **{fmt_money(amount)}**!\n"
            f"👛 New wallet: {fmt_money(user_after['cash'])}"
        ),
    )
    await ctx.send(embed=embed)


# ── ?blackjack <amount|all> ───────────────────────────────────────────────────

@bot.command(name="blackjack", aliases=["bj"])
async def prefix_blackjack(ctx, amount_str: str = ""):
    if not amount_str:
        await ctx.send("❌ Usage: `?blackjack <amount>` or `?bj all`")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    amount, err = await _resolve_amount(str(ctx.author.id), amount_str, "cash")
    if err:
        await ctx.send(err)
        return
    if ctx.author.id in active_bj_games:
        await ctx.send("❌ You already have a blackjack game in progress!")
        return
    user = await db.get_user(str(ctx.author.id))
    if user["cash"] < amount:
        await ctx.send(f"❌ You only have {fmt_money(user['cash'])} in your wallet.")
        return
    async with aiosqlite.connect(db.DB_PATH) as _db:
        await _db.execute("UPDATE users SET cash = cash - ? WHERE user_id = ?", (amount, str(ctx.author.id)))
        await _db.commit()
    deck = new_deck()
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]
    game = {"deck": deck, "player": player, "dealer": dealer, "bet": amount}
    active_bj_games[ctx.author.id] = game
    pv = hand_value(player)
    view = BlackjackView(ctx.author.id)
    if pv == 21:
        while hand_value(dealer) < 17:
            dealer.append(deck.pop())
        dv = hand_value(dealer)
        if dv == 21:
            game["outcome"], game["net"] = "push", 0.0
        else:
            game["outcome"], game["net"] = "blackjack", round(amount * 1.5, 2)
        async with aiosqlite.connect(db.DB_PATH) as _db:
            await _db.execute("UPDATE users SET cash = cash + ? WHERE user_id = ?", (amount + game["net"], str(ctx.author.id)))
            await _db.commit()
        active_bj_games.pop(ctx.author.id, None)
        await ctx.send(embed=build_bj_embed(game, finished=True))
        return
    await ctx.send(embed=build_bj_embed(game), view=view)


# ── ?roulette <bet> <amount|all> ──────────────────────────────────────────────

@bot.command(name="roulette", aliases=["rou"])
async def prefix_roulette(ctx, bet: str = "", amount_str: str = ""):
    if not bet or not amount_str:
        await ctx.send(
            "❌ Usage: `?roulette <bet> <amount>`\n"
            "Bets: `red`, `black`, `odd`, `even`, `1-18`, `19-36`, or a number `0`–`36`"
        )
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    amount, err = await _resolve_amount(str(ctx.author.id), amount_str, "cash")
    if err:
        await ctx.send(err)
        return
    multiplier, _ = roulette_payout(bet, 0)
    if multiplier == -1.0:
        await ctx.send("❌ Invalid bet. Choose: `red`, `black`, `odd`, `even`, `1-18`, `19-36`, or a number `0`–`36`.")
        return
    user = await db.get_user(str(ctx.author.id))
    if user["cash"] < amount:
        await ctx.send(f"❌ You only have {fmt_money(user['cash'])} — not enough to bet {fmt_money(amount)}.")
        return
    channel_id = ctx.channel.id
    if channel_id in active_roulette_games:
        game = active_roulette_games[channel_id]
        if any(p["user_id"] == str(ctx.author.id) for p in game["players"]):
            await ctx.send("⚠️ You're already in this game!")
            return
        async with aiosqlite.connect(db.DB_PATH) as _db:
            await _db.execute("UPDATE users SET cash = cash - ? WHERE user_id = ?", (amount, str(ctx.author.id)))
            await _db.commit()
        game["players"].append({
            "user_id": str(ctx.author.id),
            "username": ctx.author.display_name,
            "amount": amount,
            "bet": bet.lower().strip(),
        })
        try:
            await game["message"].edit(embed=build_lobby_embed(game["players"], ROULETTE_COUNTDOWN))
        except Exception:
            pass
        await ctx.send(f"✅ **{ctx.author.display_name}** joined the game! Bet: {fmt_money(amount)} on `{bet}`")
        return
    async with aiosqlite.connect(db.DB_PATH) as _db:
        await _db.execute("UPDATE users SET cash = cash - ? WHERE user_id = ?", (amount, str(ctx.author.id)))
        await _db.commit()
    players = [{"user_id": str(ctx.author.id), "username": ctx.author.display_name, "amount": amount, "bet": bet.lower().strip()}]
    msg = await ctx.send(embed=build_lobby_embed(players, ROULETTE_COUNTDOWN))
    game = {"players": players, "message": msg}
    active_roulette_games[channel_id] = game
    await asyncio.sleep(ROULETTE_COUNTDOWN)
    active_roulette_games.pop(channel_id, None)
    spin = random.randint(0, 36)
    RED_NUMBERS = {1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36}
    result_lines = []
    for p in players:
        mult, _ = roulette_payout(p["bet"], spin)
        winnings = round(p["amount"] * mult, 2)  # multiplier already includes stake on win
        async with aiosqlite.connect(db.DB_PATH) as _db:
            await _db.execute("UPDATE users SET cash = cash + ? WHERE user_id = ?", (winnings, p["user_id"]))
            await _db.commit()
        won = winnings > 0
        result_lines.append(f"{'🎉' if won else '💸'} **{p['username']}** bet `{p['bet']}` — {'won' if won else 'lost'} {fmt_money(abs(winnings))}")
    color_name = "🔴 Red" if spin in RED_NUMBERS else ("⬛ Black" if spin != 0 else "🟢 Green")
    result_embed = discord.Embed(
        title=f"🎰 Roulette — Landed on **{spin}** ({color_name})",
        color=discord.Color.gold(),
        description="\n".join(result_lines),
    )
    await msg.edit(embed=result_embed)


# ── ?steal <@user> ────────────────────────────────────────────────────────────

@bot.command(name="steal", aliases=["rob"])
async def prefix_steal(ctx, target: discord.Member = None):
    if not target:
        await ctx.send("❌ Usage: `?steal @user`")
        return
    if target.id == ctx.author.id:
        await ctx.send("❌ You can't steal from yourself.")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    await db.ensure_user(str(target.id), target.display_name)
    target_data = await db.get_user(str(target.id))
    if target_data["cash"] <= 0:
        await ctx.send(f"❌ **{target.display_name}**'s wallet is empty — nothing to steal!")
        return
    result = await db.steal_wallet(str(ctx.author.id), str(target.id))
    steal_success_rate = await db.get_bot_setting("steal_success_rate", "30")
    steal_fail_pct = await db.get_bot_setting("steal_fail_pct", "10")
    if result.get("success"):
        embed = discord.Embed(
            title="🦹 Steal Successful!",
            color=discord.Color.green(),
            description=(
                f"You swiped **{fmt_money(result['stolen'])}** from **{target.display_name}**'s wallet!\n"
                f"👛 Your new wallet: {fmt_money(result['new_cash'])}"
            ),
        )
    else:
        embed = discord.Embed(
            title="🚔 Caught in the Act!",
            color=discord.Color.red(),
            description=(
                f"You tried to steal from **{target.display_name}** but got caught!\n"
                f"💸 You lost **{fmt_money(result.get('penalty', 0))}** ({steal_fail_pct}% of your wallet) in fines.\n"
                f"👛 Your new wallet: {fmt_money(result.get('new_cash', 0))}"
            ),
        )
    embed.set_footer(text=f"Success rate: {steal_success_rate}%  ·  Fail penalty: {steal_fail_pct}% of wallet")
    await ctx.send(embed=embed)


# ── ?transfer <@user> <amount|all> ───────────────────────────────────────────

@bot.command(name="transfer", aliases=["tr"])
async def prefix_transfer(ctx, target: discord.Member = None, amount_str: str = ""):
    if not target or not amount_str:
        await ctx.send("❌ Usage: `?transfer @user <amount>` or `?transfer @user all`")
        return
    if target.id == ctx.author.id:
        await ctx.send("❌ You can't transfer to yourself.")
        return
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    await db.ensure_user(str(target.id), target.display_name)
    amount, err = await _resolve_amount(str(ctx.author.id), amount_str, "cash")
    if err:
        await ctx.send(err)
        return
    result = await db.transfer_cash(str(ctx.author.id), str(target.id), amount)
    if result == "insufficient_funds":
        user = await db.get_user(str(ctx.author.id))
        await ctx.send(f"❌ You only have {fmt_money(user['cash'])} in your wallet.")
        return
    sender = await db.get_user(str(ctx.author.id))
    embed = discord.Embed(
        title="💸 Transfer Successful",
        color=discord.Color.green(),
        description=(
            f"Sent **{fmt_money(amount)}** to **{target.display_name}**.\n"
            f"👛 Your new wallet: {fmt_money(sender['cash'])}"
        ),
    )
    await ctx.send(embed=embed)



# ── ?givebank ────────────────────────────────────────────────────────────────

@bot.command(name="givebank", aliases=["giveb", "gb"])
async def prefix_givebank(ctx, target: discord.Member = None, amount_str: str = ""):
    if not ctx.guild or not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ Only server administrators can use this command.", delete_after=8)
        return
    if not target or not amount_str:
        await ctx.send("❌ Usage: `?givebank @user <amount>`")
        return
    try:
        amount = float(amount_str)
    except ValueError:
        await ctx.send("❌ Invalid amount.")
        return
    if amount <= 0:
        await ctx.send("❌ Amount must be positive.")
        return
    await db.ensure_user(str(target.id), target.display_name)
    ok = await db.admin_give_bank(str(target.id), amount)
    if not ok:
        await ctx.send("❌ User not found.")
        return
    user_data = await db.get_user(str(target.id))
    embed = discord.Embed(
        title="🏦 Bank Cash Given",
        color=discord.Color.green(),
        description=f"Gave **{fmt_money(amount)}** to **{target.display_name}**'s bank.\nTheir new bank balance: {fmt_money(user_data['bank'])}",
    )
    await ctx.send(embed=embed)


# ── ?marketsummary ────────────────────────────────────────────────────────────

@bot.command(name="marketsummary", aliases=["ms"])
async def prefix_marketsummary(ctx):
    rows = await db.get_market_summary()
    if not rows:
        await ctx.send("📊 No market data yet.")
        return
    embed = discord.Embed(title="📊 Market Summary — Top Movers (24h)", color=discord.Color.yellow())
    lines = []
    for r in rows[:10]:
        change = r.get("change_24h", 0)
        arrow = "📈" if change > 0 else ("📉" if change < 0 else "➡️")
        sign = "+" if change >= 0 else ""
        lines.append(f"{arrow} **{r['ticker']}** {fmt_money(r['price'])}  ({sign}{fmt_money(change)})")
    embed.description = "\n".join(lines) if lines else "No data."
    await ctx.send(embed=embed)


# ── ?inventory ────────────────────────────────────────────────────────────────

@bot.command(name="inventory", aliases=["inv"])
async def prefix_inventory(ctx):
    await db.ensure_user(str(ctx.author.id), ctx.author.display_name)
    items = await db.get_user_items(str(ctx.author.id))
    embed = discord.Embed(title=f"🎒 {ctx.author.display_name}'s Inventory", color=discord.Color.og_blurple())
    if not items:
        embed.description = "Your inventory is empty! Visit `/shop` or `/market` to buy something."
    else:
        lines = []
        for it in items:
            source_icon = "🛒" if it["source"] == "shop" else "🏪"
            desc = f" — _{it['item_description']}_" if it["item_description"] else ""
            lines.append(f"{source_icon} **{it['item_name']}**{desc}")
        embed.description = "\n".join(lines)
        embed.set_footer(text=f"{len(items)} item(s)  ·  🛒 = admin shop  ·  🏪 = market")
    await ctx.send(embed=embed)


if __name__ == "__main__":
    bot.run(TOKEN)
