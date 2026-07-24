# Sanitized Alpha Vantage Fixtures

These CI-safe fixtures retain the object structure, numbered keys, string-encoded
numbers, quarterly report arrays, currency fields, and provider timestamps
observed in Alpha Vantage JSON responses. Company names, values, volumes, and
dates are synthetic so the fixtures contain no credentials or proprietary raw
payloads.

Covered provider functions:

- `GLOBAL_QUOTE`
- `OVERVIEW`
- `INCOME_STATEMENT`
- `BALANCE_SHEET`
- `CURRENCY_EXCHANGE_RATE`

Keep fields that are irrelevant to the tested behavior when they help detect a
payload-shape change. Never refresh these fixtures by committing an API key or
an unreviewed full provider response.
