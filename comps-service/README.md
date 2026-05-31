# Comps Service

Comps Service owns deterministic trading comps generation, persisted run artifacts, formula/source traces, and CSV/XLSX exports.

## Reference Methodology

The MVP trading comps methodology is based on the local reference PDF:

```text
/Users/mitanshdesai/Documents/personnel_0plus/2022.08.16 - Trading Comps copy - vF.pdf
```

## Required Input Metrics

The service needs these company-level metrics to generate an MVP comps table:

- Share price: Alpha Vantage `GLOBAL_QUOTE`
- Shares outstanding: Alpha Vantage `OVERVIEW`
- Cash: Alpha Vantage `BALANCE_SHEET`
- Debt: Alpha Vantage `BALANCE_SHEET`
- Revenue: Alpha Vantage `INCOME_STATEMENT`
- EBIT: Alpha Vantage `INCOME_STATEMENT`
- EBITDA: Alpha Vantage `INCOME_STATEMENT`
- Net income: Alpha Vantage `INCOME_STATEMENT`

The PDF also lists market cap and enterprise value as financial information to collect. For the MVP, Comps Service can calculate those values from the inputs above.

Preferred stock and non-controlling interest are part of the full enterprise value formula, but are optional for the MVP unless the provider exposes clean, reliable fields.

## Core Formulas

Full enterprise value formula from the PDF:

```text
Equity Value = Share Price x Shares Outstanding
Enterprise Value = Equity Value + Preferred Stock + Debt + Non-Controlling Interest - Cash
```

MVP simplified enterprise value formula:

```text
Enterprise Value = Equity Value + Debt - Cash
```

Trading multiples:

```text
EV / Revenue = Enterprise Value / Revenue
EV / EBIT = Enterprise Value / EBIT
EV / EBITDA = Enterprise Value / EBITDA
P/E = Equity Value / Net Income
```

## Implied Valuation

For unlevered multiples:

```text
Selected Multiple x Target Financial Metric = Implied Enterprise Value
Implied Enterprise Value - Debt + Cash = Implied Equity Value
Implied Equity Value / Shares Outstanding = Implied Share Price
```

For levered multiples:

```text
P/E Multiple x Net Income = Implied Equity Value
Implied Equity Value / Shares Outstanding = Implied Share Price
```

## MVP Simplifications

- Use simplified enterprise value: `Equity Value + Debt - Cash`.
- Ignore preferred stock and non-controlling interest unless the provider exposes clean, reliable fields.
- Use LTM actuals before analyst estimates.
- Do not generate comps from mock or synthetic company data.

## Alpha Vantage Cache Policy

Fundamental payloads are cached using the ADR-002 cache-until-next-filing strategy:

- Durable cache: PostgreSQL `fundamental_cache`
- Hot cache: Redis latest-entry cache
- Cached payloads: `OVERVIEW`, `INCOME_STATEMENT`, `BALANCE_SHEET`, and `EARNINGS_CALENDAR`
- Cache key: `(symbol, statement_type, period_type)`
- Payload format: original Alpha Vantage JSON stored as JSONB
- Refresh trigger: `next_expected_refresh_at`
- Refresh lead time: 7 days before the next expected report date
- Refresh backoff: 1 day when a refresh check finds no changed provider payload

Latest price from `GLOBAL_QUOTE` is fetched per run and is not stored in `fundamental_cache`.
