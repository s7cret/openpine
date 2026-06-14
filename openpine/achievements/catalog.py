"""Achievement catalog.

Single source of truth for the achievement list. The seed script
imports this list at first start (and on every startup, idempotently)
into the ``achievements`` table.

When you add or change an entry here, also update
``openpine-ui/src/lib/achievementCatalog.ts`` (the UI mirror). The
backend is authoritative for progress; the UI is authoritative for
copy/visual design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AchievementDef:
    id: str
    tier: str  # 'pro' | 'ultra' | 'hyper' | 'apex'
    icon: str
    title: str
    description: str
    metric: str  # matches achievement_stats.metric
    target: float
    reward: str
    hidden: bool = False


# ── Pro (25) ──────────────────────────────────────────────
PRO: list[AchievementDef] = [
    AchievementDef("bars-10k",      "pro", "📦", "10K Bars Loaded",     "Load 10,000 bars through OpenPine",                "bars_loaded",   10_000,  'Title "Loader"'),
    AchievementDef("bars-100k",     "pro", "🧪", "100K Bars Loaded",    "Load 100,000 bars through Pine → Python",           "bars_loaded",   100_000, 'Tier-up: Pro'),
    AchievementDef("bars-500k",     "pro", "📚", "500K Bars Loaded",    "Half a million bars processed",                    "bars_loaded",   500_000, 'Badge "Half-Mil"'),
    AchievementDef("first-trade",   "pro", "📈", "First Trade",         "Open your first order through OpenPine",           "trades",        1,       'Badge "Starter"'),
    AchievementDef("ten-trades",    "pro", "🎯", "10 Trades",           "10 closed orders",                                 "trades",        10,      '+0.5% allocation'),
    AchievementDef("fifty-trades",  "pro", "🪙", "50 Trades",           "50 closed orders",                                 "trades",        50,      'Title "Active"'),
    AchievementDef("strat-1",       "pro", "🌱", "First Strategy",      "Create your first strategy",                       "strategies",    1,       'Slot +1'),
    AchievementDef("strat-3",       "pro", "📊", "Strategy Trio",       "3 strategies in your portfolio",                   "strategies",    3,       'Multi-strategy slot'),
    AchievementDef("strat-5",       "pro", "🌳", "Strategy Grove",      "5 strategies running",                             "strategies",    5,       'Title "Gardener"'),
    AchievementDef("pnl-1",         "pro", "🟢", "Green Day",           "P&L ≥ +1% in a single day",                        "pnl_peak_pct",  1,       'Streak counter +1'),
    AchievementDef("pnl-5",         "pro", "💵", "+5% Week",            "Reach +5% in a 7-day window",                      "pnl_peak_pct",  5,       'Title "Profitable"'),
    AchievementDef("pnl-10",        "pro", "💰", "+10% Week",           "Reach +10% in a 7-day window",                     "pnl_peak_pct",  10,      'Frame "Bronze"'),
    AchievementDef("speed-100",     "pro", "🔄", "100 Bars/s",          "Live processing at 100 bars/sec",                  "speed_bars_sec", 100,    'Title "Live Fast"'),
    AchievementDef("speed-1k",      "pro", "⚡", "1K Bars/s",           "Live processing at 1,000 bars/sec",                "speed_bars_sec", 1_000,  'Engine priority +1'),
    AchievementDef("bt-first",      "pro", "🧬", "First Backtest",      "Run your first Pine → Python backtest",             "backtests",     1,       'Badge "Tester"'),
    AchievementDef("bt-100",        "pro", "🧫", "100 Backtests",       "Run 100 backtests",                                "backtests",     100,     'Title "Lab Rat"'),
    AchievementDef("sym-1",         "pro", "🎲", "First Symbol",        "Trade your first symbol",                          "symbols",       1,       'Watchlist slot +1'),
    AchievementDef("sym-5",         "pro", "🎰", "5 Symbols",           "Trade 5 different symbols",                        "symbols",       5,       'Watchlist slot +5'),
    AchievementDef("data-1m",       "pro", "💾", "1M Bars Cached",      "1M bars in your local data cache",                 "bars_cached",   1_000_000, 'Title "Hoarder"'),
    AchievementDef("data-multi-tf", "pro", "🗂️", "Multi-TF",            "2 timeframes in one strategy",                     "multi_tf_max",  2,       'Chart overlay unlock'),
    AchievementDef("live-1h",       "pro", "⏱️", "1 Hour Uptime",       "Live strategy runs for 1 hour",                    "live_uptime_h", 1,       'Live badge'),
    AchievementDef("live-1d",       "pro", "🌅", "1 Day Uptime",        "Live strategy runs for 24h without restart",       "live_uptime_h", 24,      'Title "Reliable"'),
    AchievementDef("tf-1m",         "pro", "1️⃣", "1-Minute Trader",     "Run a 1-minute strategy",                          "has_tf_1m",     1,       'Chart preset'),
    AchievementDef("tf-1d",         "pro", "📅", "Daily Trader",        "Run a 1-day strategy",                             "has_tf_1d",     1,       'Chart preset'),
    AchievementDef("long-short",    "pro", "↔️", "Long & Short",        "Place both a long and a short",                    "both_sides",    1,       'Title "Versatile"'),
]

# ── Ultra (28) ────────────────────────────────────────────
ULTRA: list[AchievementDef] = [
    AchievementDef("bars-1m",       "ultra", "🚀", "1M Bars Loaded",     "1 million bars processed",                         "bars_loaded",   1_000_000,  'Title "Rocket"'),
    AchievementDef("bars-5m",       "ultra", "🛰️", "5M Bars Loaded",     "5 million bars processed",                         "bars_loaded",   5_000_000,  'Frame "Satellite"'),
    AchievementDef("trades-100",    "ultra", "💎", "100 Trades",         "100 closed orders",                                "trades",        100,        'Frame "Diamond"'),
    AchievementDef("trades-500",    "ultra", "💍", "500 Trades",         "500 closed orders",                                "trades",        500,        'Title "Operator"'),
    AchievementDef("trades-1k",     "ultra", "🔥", "1K Trades",          "1,000 closed orders",                              "trades",        1_000,      'Frame "Inferno"'),
    AchievementDef("pnl-25",        "ultra", "🏔️", "+25% Month",         "+25% in a 30-day window",                          "pnl_peak_pct",  25,         'Title "Climber"'),
    AchievementDef("pnl-50",        "ultra", "🏔️", "+50% Month",         "+50% in a 30-day window",                          "pnl_peak_pct",  50,         'Frame "Silver"'),
    AchievementDef("pnl-100",       "ultra", "🗻", "100% Month",         "Double your equity in 30 days",                     "pnl_peak_pct",  100,        'Title "Everest"'),
    AchievementDef("speed-10k",     "ultra", "⚡", "10K Bars/min",       "Backtest 10,000 bars per minute",                  "speed_bars_min", 10_000,    'Engine priority +2'),
    AchievementDef("speed-100k",    "ultra", "🌊", "100K Bars/min",      "Backtest 100,000 bars per minute",                 "speed_bars_min", 100_000,   'Frame "Wave"'),
    AchievementDef("dd-10",         "ultra", "🛡️", "DD < 10%",           "Max drawdown under 10% on 1K+ bars",               "max_drawdown_pct", 10,     'Title "Cautious"'),
    AchievementDef("dd-5",          "ultra", "🛡️", "DD < 5%",            "Max drawdown under 5% on 1K+ bars",                "max_drawdown_pct", 5,      'Title "Guardian"'),
    AchievementDef("sharpe-1",      "ultra", "📐", "Sharpe ≥ 1.0",       "Sharpe ratio ≥ 1.0 on 100+ trades",                "sharpe",        1,         'Title "Quant"'),
    AchievementDef("udt-1",         "ultra", "🧬", "UDT Pioneer",        "First UDT (user-defined type) strategy",           "udt_strategies", 1,        'Type-safe slot'),
    AchievementDef("udt-5",         "ultra", "🧬", "UDT Master",         "5 UDT strategies with Pine types",                 "udt_strategies", 5,        'Theme: Type-Safe'),
    AchievementDef("strat-10",      "ultra", "🏛️", "Strategy Citadel",  "10 strategies in your portfolio",                  "strategies",    10,         'Multi-ex routing'),
    AchievementDef("sym-25",        "ultra", "🌐", "25 Symbols",         "Trade 25 different symbols",                       "symbols",       25,         'Watchlist +25'),
    AchievementDef("sym-50",        "ultra", "🌍", "50 Symbols",         "Trade 50 different symbols",                       "symbols",       50,         'Title "Cartographer"'),
    AchievementDef("ex-2",          "ultra", "🔁", "Two Exchanges",      "Trade on 2 exchanges simultaneously",              "exchanges",     2,          'Cross-EX routing'),
    AchievementDef("data-10m",      "ultra", "💽", "10M Bars Cached",    "10M bars in your local data cache",                "bars_cached",   10_000_000, 'Storage upgrade'),
    AchievementDef("data-50m",      "ultra", "💿", "50M Bars Cached",    "50M bars in your local data cache",                "bars_cached",   50_000_000, 'Title "Archivist"'),
    AchievementDef("parity-first",  "ultra", "🎬", "TV Parity Match",    "First TradingView parity match on a backtest",     "parity_matches", 1,         'Frame "Director"'),
    AchievementDef("bt-1k",         "ultra", "🏗️", "1K Backtests",       "Run 1,000 backtests",                              "backtests",     1_000,      'Title "Stress-Tester"'),
    AchievementDef("bt-parity-100", "ultra", "🎯", "100 Parity Matches", "100 TradingView parity matches",                   "parity_matches", 100,       'Title "Mirror"'),
    AchievementDef("live-7d",       "ultra", "🌄", "7 Days Uptime",      "Live strategy runs for 7 days without restart",    "live_uptime_h", 168,        'Title "Steady"'),
    AchievementDef("live-30d",      "ultra", "🗓️", "30 Days Uptime",     "Live strategy runs for 30 days without restart",   "live_uptime_h", 720,        'Frame "Calendar"'),
    AchievementDef("ast-100k",      "ultra", "🧠", "100K AST Nodes",     "Pine parser processed 100K AST nodes",             "ast_nodes",     100_000,    'Title "Parser"'),
    AchievementDef("ast-1m",        "ultra", "🧠", "1M AST Nodes",       "Pine parser processed 1M AST nodes",               "ast_nodes",     1_000_000,  'Frame "Mind"'),
]

# ── Hyper (29) ────────────────────────────────────────────
HYPER: list[AchievementDef] = [
    AchievementDef("bars-10m",      "hyper", "🌌", "10M Bars Loaded",    "10 million bars processed",                        "bars_loaded",   10_000_000,  'Title "Astronomer"'),
    AchievementDef("bars-50m",      "hyper", "🪐", "50M Bars Loaded",    "50 million bars processed",                        "bars_loaded",   50_000_000,  'Frame "Cosmos"'),
    AchievementDef("bars-100m",     "hyper", "🛸", "100M Bars Loaded",   "100 million bars processed",                       "bars_loaded",   100_000_000, 'Title "Voyager"'),
    AchievementDef("trades-10k",    "hyper", "🔥", "10K Trades",         "10,000 closed orders",                             "trades",        10_000,      'Frame "Blaze"'),
    AchievementDef("trades-50k",    "hyper", "☄️", "50K Trades",         "50,000 closed orders",                             "trades",        50_000,      'Title "Comet"'),
    AchievementDef("trades-100k",   "hyper", "🌠", "100K Trades",        "100,000 closed orders",                            "trades",        100_000,     'Frame "Meteor"'),
    AchievementDef("pnl-250",       "hyper", "💸", "+250% Peak",         "+250% all-time P&L",                               "pnl_peak_pct",  250,         'Golden ticker'),
    AchievementDef("pnl-500",       "hyper", "🪙", "+500% Peak",         "+500% all-time P&L",                               "pnl_peak_pct",  500,         'Title "Tycoon"'),
    AchievementDef("winrate-60",    "hyper", "🎯", "60% Winrate",        "60% winrate over 500+ trades",                     "winrate_pct",   60,          'Title "Streak"'),
    AchievementDef("winrate-70",    "hyper", "🎯", "70% Winrate",        "70% winrate over 500+ trades",                     "winrate_pct",   70,          'Frame "Bullseye"'),
    AchievementDef("winrate-80",    "hyper", "🎯", "80% Winrate",        "80% winrate over 500+ trades",                     "winrate_pct",   80,          'Title "Sniper"'),
    AchievementDef("dd-1",          "hyper", "🛡️", "DD < 1%",            "Max drawdown under 1% on 10K+ bars",               "max_drawdown_pct", 1,        'Title "Ironclad"'),
    AchievementDef("sharpe-2",      "hyper", "📐", "Sharpe ≥ 2.0",       "Sharpe ratio ≥ 2.0 on 1K+ trades",                 "sharpe",        2,           'Title "Quant++"'),
    AchievementDef("speed-1m",      "hyper", "🚀", "1M Bars/min",        "Backtest 1M bars per minute",                      "speed_bars_min", 1_000_000,  'Title "Hypersonic"'),
    AchievementDef("speed-10m",     "hyper", "🛩️", "10M Bars/min",       "Backtest 10M bars per minute",                     "speed_bars_min", 10_000_000, 'Frame "Jet"'),
    AchievementDef("ex-3",          "hyper", "🌍", "Three Exchanges",    "Trade on 3 exchanges simultaneously",              "exchanges",     3,           'Cross-EX++'),
    AchievementDef("sym-100",       "hyper", "🌌", "100 Symbols",        "Trade 100 different symbols",                      "symbols",       100,         'Title "Galactic"'),
    AchievementDef("mcap-top10",    "hyper", "🏆", "Top 10 by MCap",     "Trade every top-10 market cap coin",               "mcap_top10_count", 10,       'Title "Blue Chip"'),
    AchievementDef("data-100m",     "hyper", "🗄️", "100M Bars Cached",   "100M bars in your local data cache",               "bars_cached",   100_000_000, 'Frame "Vault"'),
    AchievementDef("data-500m",     "hyper", "🏢", "500M Bars Cached",   "500M bars in your local data cache",               "bars_cached",   500_000_000, 'Title "Data Center"'),
    AchievementDef("multi-tf-5",    "hyper", "🛰️", "5-TF Strategy",      "5 timeframes in one strategy",                     "multi_tf_max",  5,           'Chart overlay PRO'),
    AchievementDef("live-90d",      "hyper", "🗓️", "90 Days Uptime",     "Live strategy runs for 90 days without restart",   "live_uptime_h", 2_160,       'Title "Marathoner"'),
    AchievementDef("live-180d",     "hyper", "⏳", "180 Days Uptime",    "Live strategy runs for 180 days without restart",  "live_uptime_h", 4_320,       'Frame "Hourglass"'),
    AchievementDef("ast-10m-h",     "hyper", "🧠", "10M AST Nodes (hyper)", "Pine parser processed 10M AST nodes",            "ast_nodes",     10_000_000,   'Title "Parser-Hyper"'),
    AchievementDef("bt-10k",        "hyper", "🏭", "10K Backtests",      "Run 10,000 backtests",                             "backtests",     10_000,      'Title "Factory"'),
    AchievementDef("parity-1k",     "hyper", "🪞", "1K Parity Matches",  "1,000 TradingView parity matches",                 "parity_matches", 1_000,      'Frame "Twin"'),
]

# ── Apex (24) ─────────────────────────────────────────────
APEX: list[AchievementDef] = [
    AchievementDef("bars-500m",     "apex", "🪐", "500M Bars Loaded",   "500 million bars processed",                       "bars_loaded",   500_000_000,     'Frame "Planetary"'),
    AchievementDef("bars-1b",       "apex", "🌌", "1 Billion Bars Loaded", "1,000,000,000 bars through gateway",          "bars_loaded",   1_000_000_000,   'Golden frame + secret ID'),
    AchievementDef("bars-10b",      "apex", "🌠", "10 Billion Bars",    "10 billion bars processed",                        "bars_loaded",   10_000_000_000,  'Title "Cosmos Walker"'),
    AchievementDef("bars-100b",     "apex", "✨", "100 Billion Bars",   "100 billion bars processed",                       "bars_loaded",   100_000_000_000, 'Title "Universe"'),
    AchievementDef("trades-1m",     "apex", "🌋", "1M Trades",          "1,000,000 closed orders",                          "trades",        1_000_000,       'Frame "Volcano"'),
    AchievementDef("trades-10m",    "apex", "🌅", "10M Trades",         "10,000,000 closed orders",                         "trades",        10_000_000,      'Title "Sunrise"'),
    AchievementDef("trades-100m",   "apex", "🌌", "100M Trades",        "100,000,000 closed orders",                        "trades",        100_000_000,     'Frame "Horizon"'),
    AchievementDef("pnl-10000",     "apex", "🪙", "+10000% Peak",       "+10000% all-time P&L",                             "pnl_peak_pct",  10_000,          'Title "Whale"'),
    AchievementDef("winrate-95",    "apex", "🎯", "95% Winrate",        "95% winrate over 1,000+ trades",                   "winrate_pct",   95,              'Frame "Crown"'),
    AchievementDef("dd-zero",       "apex", "🛡️", "Zero Drawdown",      "Zero drawdown over 10K+ bars",                     "max_drawdown_pct", 0,           'Title "Untouched"'),
    AchievementDef("sharpe-3",      "apex", "📐", "Sharpe ≥ 3.0",       "Sharpe ratio ≥ 3.0 on 10K+ trades",                "sharpe",        3,               'Title "Sigma"'),
    AchievementDef("live-365d",     "apex", "🦄", "Uptime 99.99% (1 year)", "Live strategy without restart 365 days",       "live_uptime_h", 8_760,           'Title "Immortal"'),
    AchievementDef("live-3y",       "apex", "⏰", "3 Years Uptime",     "Live strategy without restart 3 years",           "live_uptime_h", 26_280,          'Frame "Eternal"'),
    AchievementDef("ex-5",          "apex", "🗺️", "Five Exchanges",     "Trade on 5 exchanges simultaneously",              "exchanges",     5,               'Cross-EX Ultra'),
    AchievementDef("sym-500",       "apex", "🌠", "500 Symbols",        "Trade 500 different symbols",                      "symbols",       500,             'Title "Cartographer++"'),
    AchievementDef("data-1b",       "apex", "🏛️", "1B Bars Cached",     "1 billion bars in your local data cache",          "bars_cached",   1_000_000_000,   'Title "Library of Babel"'),
    AchievementDef("data-10b",      "apex", "🌌", "10B Bars Cached",    "10 billion bars in your local data cache",         "bars_cached",   10_000_000_000,  'Frame "Black Hole"'),
    AchievementDef("ast-10m",       "apex", "🧠", "10M AST Nodes",      "Pine parser processed 10M AST nodes",              "ast_nodes",     10_000_000,      'Title "Cortex"'),
    AchievementDef("ast-100m",      "apex", "🧠", "100M AST Nodes",     "Pine parser processed 100M AST nodes",             "ast_nodes",     100_000_000,     'Frame "Genesis"'),
    AchievementDef("shipped-lib",   "apex", "🏛️", "Shipped Library",    "Publish a Pine library in OpenPine registry",      "shipped_lib",   1,               'Maintainer badge'),
    AchievementDef("ruin-recovery", "apex", "💀", "Ruin Recovery",      "Lose -90% then recover to +100%",                  "ruin_recovery", 1,               'Frame "Phoenix"'),
    AchievementDef("secret-buy-zero", "apex", "❓", '"I thought you couldn\'t"', "Try calling strategy.entry(\"BUY\", 0)",      "secret_buy_zero", 1,             '???', hidden=True),
    AchievementDef("secret-nuclear",  "apex", "❓", "Nuclear Launch Detected",  "strategy.risk.max_drawdown(999, ...)",      "secret_nuclear", 1,              '???', hidden=True),
]

ALL: list[AchievementDef] = PRO + ULTRA + HYPER + APEX


def by_metric() -> dict[str, list[AchievementDef]]:
    """Group achievements by their source-of-truth metric."""
    out: dict[str, list[AchievementDef]] = {}
    for a in ALL:
        out.setdefault(a.metric, []).append(a)
    return out
