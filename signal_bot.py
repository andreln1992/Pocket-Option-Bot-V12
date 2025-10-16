"""
signal_bot.py
Manual signal generator using Deriv WebSocket market data.
- Install dependencies: pip install websockets asyncio requests
- Run: python signal_bot.py
- Type commands at the prompt, e.g.:
    signal EURUSD_OTC timeframe=1m expiration=2m
    list
    quit
Notes:
- This script only generates signals; it DOES NOT place trades on Pocket Option.
- Adjust strategy in compute_signal() as needed.
"""

import asyncio
import json
import time
import statistics
import websockets
from collections import deque

# ========== CONFIG ==========
DERIV_WS_URL = "wss://ws.deriv.com/websockets/v3?app_id=1089"  # replace app_id if you have one
DERIV_TOKEN = None  # If you want account-level access, set your token here (not needed for public market data)
# Map user-friendly instrument names to Deriv symbols. Verify exact names with Deriv's active_symbols.
SYMBOL_MAP = {
    "EURUSD_OTC": "frxEURUSD",     # example — verify on Deriv
    "GBPUSD_OTC": "frxGBPUSD",
    "AUDUSD_OTC": "frxAUDUSD",
    # add more pairs (OTC) here
}

# Max number of price points stored per timeframe
MAX_POINTS = 500

# ========== UTILITIES ==========
def parse_duration(s):
    """Parse timeframe or expiration like '1m', '5m', '15s' into seconds."""
    if s.endswith('m'):
        return int(s[:-1]) * 60
    if s.endswith('s'):
        return int(s[:-1])
    if s.endswith('h'):
        return int(s[:-1]) * 3600
    raise ValueError("Invalid duration format, use e.g. 1m, 30s, 1h")

# ========== Market Data Manager ==========
class MarketData:
    def __init__(self):
        # store ticks per symbol: deque of (timestamp, price)
        self.ticks = {}

    def ensure_symbol(self, symbol):
        if symbol not in self.ticks:
            self.ticks[symbol] = deque(maxlen=MAX_POINTS)

    def add_tick(self, symbol, price, ts=None):
        self.ensure_symbol(symbol)
        if ts is None:
            ts = time.time()
        self.ticks[symbol].append((ts, float(price)))

    def get_prices_since(self, symbol, since_seconds):
        """Return list of prices from last since_seconds seconds."""
        self.ensure_symbol(symbol)
        cutoff = time.time() - since_seconds
        return [p for (t,p) in self.ticks[symbol] if t >= cutoff]

market = MarketData()

# ========== SIGNAL STRATEGY ==========
def compute_signal(prices, fast_len=5, slow_len=20):
    """
    Simple moving average crossover:
    - if fast_ma > slow_ma and slope positive -> BUY
    - if fast_ma < slow_ma and slope negative -> SELL
    - otherwise HOLD
    Returns: (signal:str, info:str)
    """
    if len(prices) < max(fast_len, slow_len) or len(prices) < 3:
        return ("HOLD", "not enough data")
    # use last contiguous prices
    fast_ma = statistics.mean(prices[-fast_len:])
    slow_ma = statistics.mean(prices[-slow_len:]) if len(prices) >= slow_len else statistics.mean(prices)
    # slope of last 3 prices as a very simple momentum check
    slope = (prices[-1] - prices[-3]) / 2.0
    if fast_ma > slow_ma and slope > 0:
        return ("BUY", f"fast_ma {fast_ma:.5f} > slow_ma {slow_ma:.5f}, slope {slope:.6f}")
    if fast_ma < slow_ma and slope < 0:
        return ("SELL", f"fast_ma {fast_ma:.5f} < slow_ma {slow_ma:.5f}, slope {slope:.6f}")
    return ("HOLD", f"fast_ma {fast_ma:.5f}, slow_ma {slow_ma:.5f}, slope {slope:.6f}")

# ========== WEBSOCKET LISTENER ==========
async def deriv_ws_listener():
    async with websockets.connect(DERIV_WS_URL) as ws:
        # optional: authorize if you have a token (not required for market data subscribe)
        if DERIV_TOKEN:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            auth_resp = json.loads(await ws.recv())
            print("Auth response:", auth_resp)

        print("Connected to Deriv websockets.")
        # Keep listening and dispatch ticks to market manager
        while True:
            msg = await ws.recv()
            try:
                data = json.loads(msg)
            except Exception:
                continue
            # Deriv sends ticks inside 'tick' objects when you subscribe
            if 'tick' in data:
                t = data['tick']
                symbol = t.get('symbol') or t.get('quote') or data.get('echo_req', {}).get('subscribe')  # best-effort
                price = t.get('quote') or t.get('price') or t.get('bid') or t.get('ask')
                if symbol and price is not None:
                    market.add_tick(symbol, price, ts=t.get('epoch', time.time()))
            # We ignore other messages here (subscription confirmations etc.)
            # To subscribe, the main thread will send subscribe requests to this websocket
            # (We use a simpler pattern: this listener only receives; subscriptions can be made by separate short-lived connections)

# Helper to create a subscription and fetch a short snapshot using a temporary connection
async def subscribe_and_fetch(symbol, duration_seconds=60):
    """Subscribe to ticks for symbol and collect for duration_seconds (non-blocking if short)."""
    url = DERIV_WS_URL
    collected = []
    async with websockets.connect(url) as ws:
        # optional auth if DERIV_TOKEN
        if DERIV_TOKEN:
            await ws.send(json.dumps({"authorize": DERIV_TOKEN}))
            _ = json.loads(await ws.recv())
        # subscribe to ticks (Deriv 'ticks' request)
        req = {"ticks": symbol, "subscribe": 1}
        await ws.send(json.dumps(req))
        start = time.time()
        while time.time() - start < duration_seconds:
            msg = await asyncio.wait_for(ws.recv(), timeout=duration_seconds)
            data = json.loads(msg)
            if 'tick' in data:
                t = data['tick']
                price = t.get('quote') or t.get('price')
                epoch = t.get('epoch', time.time())
                collected.append((epoch, float(price)))
                # also keep in the global market cache
                market.add_tick(symbol, price, ts=epoch)
        # unsubscribe
        await ws.send(json.dumps({"forget": req}))
    return collected

# ========== USER INTERFACE (console) ==========
def format_symbol(user_sym):
    # map user friendly name to deriv symbol
    return SYMBOL_MAP.get(user_sym, user_sym)

async def handle_signal_command(user_sym, timeframe='1m', expiration='2m'):
    deriv_sym = format_symbol(user_sym)
    tf_seconds = parse_duration(timeframe)
    exp_seconds = parse_duration(expiration)

    # Make sure we have some recent data: if not, quickly fetch a short sample (2 * timeframe)
    lookback = tf_seconds * 3
    prices = market.get_prices_since(deriv_sym, lookback)
    if len(prices) < max(10, tf_seconds // 1):
        # fetch short sample (non-blocking short wait)
        print(f"[info] not enough cached ticks for {deriv_sym}, fetching {max(5, tf_seconds)}s snapshot from Deriv...")
        try:
            collected = await subscribe_and_fetch(deriv_sym, duration_seconds=min(10, max(5, tf_seconds)))
            prices = market.get_prices_since(deriv_sym, lookback)
        except Exception as e:
            print("[error] failed to fetch data:", e)
            return {"signal":"HOLD", "reason": "failed to retrieve data"}

    # aggregate prices into timeframe bars (simple: take last price per second and then sample per timeframe)
    # For simplicity we'll use the raw tick prices in last tf_seconds window
    window_prices = market.get_prices_since(deriv_sym, tf_seconds)
    if not window_prices:
        return {"signal":"HOLD", "reason":"no recent prices"}

    signal, info = compute_signal(window_prices, fast_len=max(3, tf_seconds//6), slow_len=max(8, tf_seconds//2))
    result = {
        "symbol": deriv_sym,
        "user_symbol": user_sym,
        "timeframe": timeframe,
        "expiration": expiration,
        "signal": signal,
        "info": info,
        "last_price": window_prices[-1],
        "timestamp": time.time()
    }
    return result

async def main_console():
    # Start a background listener to keep collecting ticks (optional)
    listener_task = asyncio.create_task(deriv_ws_listener())
    print("Manual signal bot. Type 'help' for commands.")
    try:
        while True:
            raw = await asyncio.get_event_loop().run_in_executor(None, input, ">> ")
            if not raw:
                continue
            parts = raw.strip().split()
            cmd = parts[0].lower()
            if cmd in ('quit','exit'):
                print("Exiting...")
                listener_task.cancel()
                break
            if cmd == 'help':
                print("Commands:\n  signal <PAIR> [timeframe=1m] [expiration=2m]\n  list  -> show SYMBOL_MAP\n  add <PAIR> <DERIV_SYMBOL>\n  quit\n")
                continue
            if cmd == 'list':
                print("Known pairs:")
                for k,v in SYMBOL_MAP.items():
                    print(f"  {k} -> {v}")
                continue
            if cmd == 'add' and len(parts) >= 3:
                SYMBOL_MAP[parts[1]] = parts[2]
                print("Added:", parts[1], "->", parts[2])
                continue
            if cmd == 'signal':
                # parse args
                if len(parts) < 2:
                    print("Usage: signal <PAIR> [timeframe=1m] [expiration=2m]")
                    continue
                pair = parts[1]
                timeframe = '1m'
                expiration = '2m'
                for p in parts[2:]:
                    if p.startswith('timeframe='):
                        timeframe = p.split('=',1)[1]
                    if p.startswith('expiration='):
                        expiration = p.split('=',1)[1]
                res = await handle_signal_command(pair, timeframe=timeframe, expiration=expiration)
                print("----- SIGNAL -----")
                print(f"{res.get('signal')} for {res.get('user_symbol')} (deriv: {res.get('symbol')})")
                print("Price:", res.get('last_price'))
                print("Timeframe:", res.get('timeframe'), "Expiration:", res.get('expiration'))
                print("Reason:", res.get('info'))
                print("------------------")
                continue
            print("unknown command. Type help")
    finally:
        if not listener_task.done():
            listener_task.cancel()

if __name__ == "__main__":
    asyncio.run(main_console())
