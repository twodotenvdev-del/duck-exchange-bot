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
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await db.init_db()
        await self.tree.sync()
        print("Slash commands synced.")

    async def on_ready(self):
        print(f"Duck Exchange is online as {self.user} (ID: {self.user.id})")
        if not stock_fluctuation.is_running():
            stock_fluctuation.start()


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
                arrow = "📈" if r["change"] > 0 else "📉"
                sign = "+" if r["change"] > 0 else ""
                lines.append(f"{arrow} **{r['ticker']}**  {fmt_money(r['old'])} → {fmt_money(r['new'])}  ({sign}{fmt_money(r['change'])})")
            print(f"[Fluctuation] {len(changed)}/{len(results)} stocks moved")


def is_admin(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    return interaction.permissions.administrator


async def ensure(interaction: discord.Interaction):
    await db.ensure_user(str(interaction.user.id), interaction.user.display_name)


def render_chart_image(prices: list[float], ticker: str, name: str, shareholders: int = 0) -> io.BytesIO:
    """Render a candlestick-style chart image and return it as a BytesIO PNG."""
    BG      = "#0d1b2a"
    GRID    = "#1a2e42"
    TEXT    = "#dce8f0"
    DIM     = "#5a7a90"
    GREEN   = "#26a65b"
    RED     = "#e84040"
    NEUTRAL = "#7a8fa0"

    price_range = max(prices) - min(prices) if max(prices) != min(prices) else prices[0] * 0.1
    wick_ext = price_range * 0.03

    MAX_SLOTS = 40
    fig, ax = plt.subplots(figsize=(14, 4))
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    body_width = 0.95
    wick_lw    = 1.2

    for i, price in enumerate(prices):
        if i == 0:
            half = max(price_range * 0.015, price * 0.005)
            body_lo, body_hi = price - half, price + half
            color = NEUTRAL
        else:
            prev = prices[i - 1]
            body_lo = min(prev, price)
            body_hi = max(prev, price)
            color = GREEN if price >= prev else RED

        wick_lo = body_lo - wick_ext
        wick_hi = body_hi + wick_ext

        ax.plot([i, i], [wick_lo, wick_hi], color=color, linewidth=wick_lw, zorder=2, solid_capstyle="round")

        body_h = max(body_hi - body_lo, price_range * 0.004)
        rect = mpatches.FancyBboxPatch(
            (i - body_width / 2, body_lo), body_width, body_h,
            boxstyle="square,pad=0", linewidth=0, facecolor=color, zorder=3,
        )
        ax.add_patch(rect)

    ax.set_xlim(-0.5, MAX_SLOTS - 0.5)  # always 40 slots wide
    ax.set_ylim(min(prices) - wick_ext * 3, max(prices) + wick_ext * 6)

    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.2f}"))
    ax.yaxis.grid(True, color=GRID, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.xaxis.set_visible(False)
    for spine in ax.spines.values():
        spine.set_edgecolor(GRID)
    ax.tick_params(colors=TEXT, labelsize=8.5)

    ax.set_title(f"{ticker}  —  {name}", color=TEXT, fontsize=13, fontweight="bold", pad=10)

    overall = prices[-1] - prices[0]
    pct = (overall / prices[0] * 100) if prices[0] != 0 else 0
    arrow = "▲" if overall >= 0 else "▼"
    change_color = GREEN if overall >= 0 else RED

    holder_str = f"   ·   👥 {shareholders:,} shareholder{'s' if shareholders != 1 else ''}"
    fig.text(
        0.5, 0.912,
        f"${prices[-1]:,.2f}   {arrow} ${abs(overall):,.2f} ({pct:+.1f}%){holder_str}",
        ha="center", fontsize=9, color=change_color,
    )

    plt.tight_layout(rect=[0, 0, 1, 0.905])

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=140, facecolor=BG)
    plt.close(fig)
    buf.seek(0)
    return buf


# ── /stocks ──────────────────────────────────────────────────────────────────

@bot.tree.command(name="stocks", description="List all available stocks and their current prices.")
async def stocks_cmd(interaction: discord.Interaction):
    await interaction.response.defer()
    rows = await db.get_all_stocks()
    if not rows:
        await interaction.followup.send("No stocks have been created yet. An admin can use `/createstock` to add one.")
        return

    embed = discord.Embed(
        title="🦆 Duck Exchange — Stock Market",
        color=discord.Color.yellow(),
    )
    lines = []
    for row in rows:
        lines.append(f"**{row['ticker']}** — {row['name']} — {fmt_money(row['price'])}")
    embed.description = "\n".join(lines)
    embed.set_footer(text="Use /buy <stock> <amount> to invest!")
    await interaction.followup.send(embed=embed)


# ── /portfolio ────────────────────────────────────────────────────────────────

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
            value = h["shares"] * h["price"]
            lines.append(
                f"**{h['ticker']}** ({h['name']}) — {h['shares']:,} shares @ {fmt_money(h['price'])} = {fmt_money(value)}"
            )
        embed.add_field(name="📊 Shares Owned", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="📊 Shares Owned", value="None — use `/buy` to get started!", inline=False)

    await interaction.followup.send(embed=embed)


# ── /buy ──────────────────────────────────────────────────────────────────────

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
    owned = sum(h["shares"] for h in holdings)

    await interaction.followup.send(
        f"❌ You can only own **30 total shares**.\n"
        f"You currently own: **{owned}/30**\n"
        f"Trying to buy: **{amount}**"
    )
    return

    if result == "insufficient_funds":
        user = await db.get_user(str(interaction.user.id))
        await interaction.followup.send(
            f"❌ You don't have enough cash.\n"
            f"**Cost:** {fmt_money(cost)}\n"
            f"**Your cash:** {fmt_money(user['cash'])}"
        )
        return

    new_price = result
    user = await db.get_user(str(interaction.user.id))
    embed = discord.Embed(
        title="✅ Purchase Successful",
        color=discord.Color.green(),
        description=(
            f"You bought **{amount:,} shares** of **{ticker}** ({stock['name']}) "
            f"at {fmt_money(stock['price'])} each.\n"
            f"**Total cost:** {fmt_money(cost)}\n"
            f"**Remaining cash:** {fmt_money(user['cash'])}\n"
            f"**New price:** {fmt_money(stock['price'])} → 📈 {fmt_money(new_price)} (+{fmt_money(amount * db.PRICE_IMPACT_BUY)})"
        ),
    )
    await interaction.followup.send(embed=embed)


# ── /sell ─────────────────────────────────────────────────────────────────────

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

    proceeds = amount * stock["price"]
    result = await db.sell_stock(str(interaction.user.id), ticker, amount, stock["price"])

    if result == "insufficient_shares":
        holding = await db.get_holding(str(interaction.user.id), ticker)
        owned = holding["shares"] if holding else 0
        await interaction.followup.send(
            f"❌ You don't have enough shares of **{ticker}**.\n"
            f"**Requested:** {amount:,}\n"
            f"**You own:** {owned:,}"
        )
        return

    new_price = result
    user = await db.get_user(str(interaction.user.id))
    embed = discord.Embed(
        title="✅ Sale Successful",
        color=discord.Color.blue(),
        description=(
            f"You sold **{amount:,} shares** of **{ticker}** ({stock['name']}) "
            f"at {fmt_money(stock['price'])} each.\n"
            f"**Proceeds:** {fmt_money(proceeds)}\n"
            f"**New cash balance:** {fmt_money(user['cash'])}\n"
            f"**New price:** {fmt_money(stock['price'])} → 📉 {fmt_money(new_price)} (-{fmt_money(amount * db.PRICE_IMPACT_SELL)})"
        ),
    )
    await interaction.followup.send(embed=embed)


# ── /chart ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="chart", description="Show the price history chart for a stock.")
@app_commands.describe(ticker="Stock ticker symbol (e.g. DUCK)")
async def chart_cmd(interaction: discord.Interaction, ticker: str):
    await interaction.response.defer()

    ticker = ticker.upper()
    stock = await db.get_stock(ticker)
    if not stock:
        await interaction.followup.send(f"❌ No stock with ticker **{ticker}** found. Use `/stocks` to see available stocks.")
        return

    history = await db.get_price_history(ticker, limit=40)
    if len(history) < 2:
        await interaction.followup.send(
            f"📊 **{ticker}** — {stock['name']}\n"
            f"Current price: {fmt_money(stock['price'])}\n\n"
            f"Not enough price history yet. Admins need to change the price at least once to generate a chart."
        )
        return

    prices = [row["price"] for row in history]
    owners = await db.get_owners_of_stock(ticker)
    buf = render_chart_image(prices, ticker, stock["name"], shareholders=len(owners))
    await interaction.followup.send(file=discord.File(buf, filename=f"{ticker}_chart.png"))


# ── /leaderboard ──────────────────────────────────────────────────────────────

@bot.tree.command(name="leaderboard", description="See the richest users in Duck Exchange.")
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


# ── Admin: /createstock ───────────────────────────────────────────────────────

@bot.tree.command(name="createstock", description="[Admin] Create a new stock.")
@app_commands.describe(
    ticker="Short ticker symbol (e.g. DUCK)",
    name="Full stock name (e.g. Duck Inc.)",
    starting_price="Starting price per share",
)
async def createstock_cmd(
    interaction: discord.Interaction,
    ticker: str,
    name: str,
    starting_price: float,
):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Only server administrators can create stocks.", ephemeral=True)
        return

    if starting_price <= 0:
        await interaction.response.send_message("❌ Starting price must be greater than 0.", ephemeral=True)
        return

    ticker = ticker.upper()
    success = await db.create_stock(ticker, name, starting_price)
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
            f"**Starting price:** {fmt_money(starting_price)}"
        ),
    )
    await interaction.response.send_message(embed=embed)


# ── Admin: /setprice ──────────────────────────────────────────────────────────

@bot.tree.command(name="setprice", description="[Admin] Set the price of a stock.")
@app_commands.describe(ticker="Stock ticker symbol", new_price="New price per share")
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
    result = await db.withdraw(str(interaction.user.id), amount)
    if result == "insufficient_funds":
        user = await db.get_user(str(interaction.user.id))
        await interaction.response.send_message(
            f"❌ You only have {fmt_money(user['bank'])} in your bank.", ephemeral=True
        )
        return
    user = await db.get_user(str(interaction.user.id))
    embed = discord.Embed(
        title="🏦 Withdrawal Successful",
        color=discord.Color.green(),
        description=(
            f"Withdrew **{fmt_money(amount)}** from your bank.\n"
            f"👛 Wallet: {fmt_money(user['cash'])}  |  🏦 Bank: {fmt_money(user['bank'])}"
        ),
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

@bot.tree.command(name="steal", description="Attempt to steal another player's wallet (30% success, 5 min cooldown).")
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

@bot.tree.command(name="claim", description="Claim $100 every 60 seconds!")
async def claim_cmd(interaction: discord.Interaction):
    await ensure(interaction)
    result = await db.claim_daily(str(interaction.user.id))
    if not result.get("ok"):
        secs = result.get("seconds_left", 0)
        m, s = divmod(secs, 60)
        time_str = f"{m}m {s}s" if m else f"{s}s"
        await interaction.response.send_message(
            f"⏳ Already claimed! Come back in **{time_str}**.", ephemeral=True
        )
        return
    embed = discord.Embed(
        title="✅ Claimed $100!",
        color=discord.Color.green(),
        description=f"💵 Added **$100.00** to your wallet.\n👛 New wallet balance: {fmt_money(result['new_cash'])}",
    )
    embed.set_footer(text="You can claim again in 60 seconds!")
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


@bot.tree.command(name="work", description="Work a job and earn $50–$200 (3 min cooldown).")
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
    msg = random.choice(WORK_MESSAGES)
    embed = discord.Embed(
        title="💼 Work Complete",
        color=discord.Color.blue(),
        description=(
            f"{msg} and earned **{fmt_money(result['earned'])}**!\n\n"
            f"👛 New wallet: {fmt_money(result['new_cash'])}"
        ),
    )
    embed.set_footer(text="Cooldown: 3 minutes")
    await interaction.response.send_message(embed=embed)


# ── /crime ────────────────────────────────────────────────────────────────────

@bot.tree.command(name="crime", description="Commit a crime for $100–$500 (5 min cooldown, 50% fail = -30% wealth).")
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
                f"💸 You lost **{fmt_money(result['penalty'])}** (30% of your wealth) in fines!\n"
                f"👛 Wallet: {fmt_money(result['new_cash'])}  |  🏦 Bank: {fmt_money(result['new_bank'])}"
            ),
        )
    embed.set_footer(text="Cooldown: 5 minutes")
    await interaction.response.send_message(embed=embed)


# ── /shop ─────────────────────────────────────────────────────────────────────

@bot.tree.command(name="shop", description="Browse the admin shop and buy items.")
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
    item = await db.get_shop_item(item_id)
    # item may be None now if it was last in stock, fetch before buying
    embed = discord.Embed(
        title="✅ Purchase Successful",
        color=discord.Color.green(),
        description=f"You bought an item from the shop! Check `/inventory` to see it.",
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="createitem", description="[Admin] Add an item to the shop.")
@app_commands.describe(name="Item name", price="Price in cash", description="Item description", stock="Stock amount (-1 = unlimited)")
async def createitem_cmd(interaction: discord.Interaction, name: str, price: float, description: str = "", stock: int = -1):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    if price <= 0:
        await interaction.response.send_message("❌ Price must be positive.", ephemeral=True)
        return
    item_id = await db.create_shop_item(name, description, price, stock)
    stock_str = "Unlimited" if stock == -1 else str(stock)
    embed = discord.Embed(
        title="✅ Item Created",
        color=discord.Color.green(),
        description=f"**[#{item_id}] {name}**\n{description}\n\nPrice: {fmt_money(price)}  |  Stock: {stock_str}",
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="deleteitem", description="[Admin] Remove an item from the shop.")
@app_commands.describe(item_id="Item ID to delete")
async def deleteitem_cmd(interaction: discord.Interaction, item_id: int):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ Admins only.", ephemeral=True)
        return
    ok = await db.delete_shop_item(item_id)
    if not ok:
        await interaction.response.send_message(f"❌ No item with ID **#{item_id}** found.", ephemeral=True)
        return
    await interaction.response.send_message(f"🗑️ Item **#{item_id}** deleted from the shop.", ephemeral=True)


@bot.tree.command(name="edititem", description="[Admin] Edit a shop item's name, price, stock, or description.")
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


if __name__ == "__main__":
    bot.run(TOKEN)
