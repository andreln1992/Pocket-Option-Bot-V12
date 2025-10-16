# Pocket Signal Bot (Manual Deriv API Signal Generator)

A manual signal generator that uses **Deriv WebSocket API** to fetch live forex/OTC prices and gives **BUY/SELL/HOLD** signals on demand — similar to Pocket Option style trading.

⚠️ This bot does **not** execute trades automatically — it only provides signals.  
You can use the signals manually in Pocket Option, Deriv, or any other platform.

---

## 🚀 Features
- Works with **Deriv WebSocket API**
- Supports **OTC forex pairs**
- Customizable **timeframes** and **expiration times**
- Simple **moving average crossover** strategy (editable)
- Real-time market data
- CLI interface for requesting signals
- Easily extensible for more indicators or auto-execution

---

## 🛠 Setup

### 1. Clone this repository
```bash
git clone https://github.com/<your-username>/pocket-signal-bot.git
cd pocket-signal-bot
