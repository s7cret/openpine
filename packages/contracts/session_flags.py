"""Versioned instrument-session references for the UTC 24×7 profile."""

IS_MARKET = 'session.ismarket'
MARKET_FLAGS = (IS_MARKET, 'session.ispremarket', 'session.ispostmarket')
FIRST_FLAGS = ('session.isfirstbar', 'session.isfirstbar_regular')
LAST_FLAGS = ('session.islastbar', 'session.islastbar_regular')
BOUNDARY_FLAGS = FIRST_FLAGS + LAST_FLAGS
VERSIONS = dict.fromkeys(MARKET_FLAGS, 4) | dict.fromkeys(BOUNDARY_FLAGS, 5)
SLOTS = {name: 'environment.' + name for name in VERSIONS}
