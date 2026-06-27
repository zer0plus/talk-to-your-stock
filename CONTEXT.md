# TalkToYourStock

TalkToYourStock is a chat-first fundamental analysis product for generating auditable trading comps from user messages. The MVP focuses on deterministic comps table generation and conversational explanation around those results.

## Language

**User**:
The authenticated person using TalkToYourStock.
_Avoid_: Customer, account holder

**Thread**:
A persisted chat conversation owned by one User.
_Avoid_: Conversation, chat session

**Message**:
A single user or assistant entry inside a Thread.
_Avoid_: Chat item, utterance

**Agent**:
The Google ADK-powered reasoning layer that interprets Messages and decides whether to answer conversationally, ask for clarification, or call a Tool.
_Avoid_: Bot, assistant service

**Fundamental Analysis Agent**:
The only active MVP Agent. It answers stock and fundamentals questions and triggers deterministic comps generation when needed.
_Avoid_: Stock agent, finance bot

**Tool**:
A deterministic capability invoked by the Agent through an explicit contract.
_Avoid_: Plugin, function unless discussing implementation mechanics

**Comps**:
Trading comparables analysis that compares a target company against peer companies using valuation metrics.
_Avoid_: Comparable companies analysis, peer table unless user-facing copy requires it

**Comps Table**:
The tabular output of a comps run, containing company rows and deterministic valuation metrics.
_Avoid_: Spreadsheet, valuation table

**Run**:
A persisted execution record created only when a Message triggers table-generation comps work.
_Avoid_: Job, task

**Source Snapshot**:
The preserved provider data used for a Run so outputs can be audited and reproduced.
_Avoid_: Raw data dump, cache entry

**Trace**:
The formula, input, and source-field explanation for a computed comps value or Run output.
_Avoid_: Log, audit trail

**Ticker**:
The canonical exchange symbol used to identify a company for market and fundamental data lookups.
_Avoid_: Stock symbol when naming domain entities

**Target Ticker**:
The primary Ticker the User wants to analyze.
_Avoid_: Main stock, subject company

**Peer Ticker**:
A Ticker used as a comparison company in Comps.
_Avoid_: Comparable, comp unless referring to the overall analysis

**Peer Selection Mode**:
Whether Peer Tickers were supplied by the User or selected automatically by the Comps Service.
_Avoid_: Selection strategy

**Metric**:
A deterministic financial or valuation value shown in a Comps Table.
_Avoid_: Field, datapoint

**Fundamentals**:
Company financial statement data used to calculate valuation metrics.
_Avoid_: Financials when naming persistent concepts

**Fundamental Cache**:
The durable latest-filing store for Alpha Vantage fundamentals, backed by PostgreSQL JSONB and Redis hot reads.
_Avoid_: Facts table, warehouse

**Quote**:
Market price data used alongside Fundamentals to compute valuation metrics.
_Avoid_: Market data when the concept is specifically price-oriented

**Export**:
A downloadable CSV or XLSX representation of a Comps Table owned by the Comps Service in the MVP.
_Avoid_: Report

**Web BFF**:
The user-facing backend boundary that verifies auth, persists Thread and Message state, calls the Agent Service, and streams progress to the web app.
_Avoid_: API server, backend

**Agent Service**:
The service boundary that hosts Google ADK orchestration and the Fundamental Analysis Agent.
_Avoid_: Orchestrator service unless discussing implementation mechanics

**Comps Service**:
The service boundary that owns deterministic comps calculations, provider fetch behavior, source snapshots, traces, async workers, and MVP exports.
_Avoid_: Calculation service, data service

**Conversation Response**:
An assistant reply that does not create a Run because no comps table generation is needed.
_Avoid_: Normal response, free chat
