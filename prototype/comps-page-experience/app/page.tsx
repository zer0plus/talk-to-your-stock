"use client";

// PROTOTYPE ONLY: Three desktop Comps Page concepts, switchable via ?variant=.

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

type VariantKey = "A" | "B" | "C";
type DemoState = "arrival" | "waiting" | "success" | "input-error" | "failed";

type Thread = {
  id: string;
  user_id: string;
  title: string;
  message_count: number;
  last_message_at: string | null;
  latest_run_id: string | null;
  created_at: string;
  updated_at: string;
};

type CompsRow = {
  ticker: string;
  company_name: string | null;
  is_target: boolean;
  currency: string;
  share_price: number | null;
  market_cap: number | null;
  enterprise_value: number | null;
  revenue_ltm: number | null;
  ebitda_ltm: number | null;
  ev_to_revenue: number | null;
  ev_to_ebitda: number | null;
  pe: number | null;
  as_of: string;
};

const RUN_ID = "612d1a39-1453-4fe2-9c63-f916d1e85f94";
const USER_ID = "72384531-04a0-4cb8-a62f-cb49bdb81572";
const DEFAULT_PROMPT = "Compare Apple with Microsoft, Alphabet, and Meta.";
const INCOMPLETE_PROMPT = "Compare Apple with Microsoft.";

const threads: Thread[] = [
  {
    id: "0fd4061b-2bb3-4bdd-ab0c-4d55a8c9a836",
    user_id: USER_ID,
    title: "Apple vs mega-cap peers",
    message_count: 4,
    last_message_at: "2026-08-01T14:33:00Z",
    latest_run_id: RUN_ID,
    created_at: "2026-08-01T14:28:00Z",
    updated_at: "2026-08-01T14:33:00Z",
  },
  {
    id: "68eb4a65-49b2-4dc0-bef0-d064329f0e9e",
    user_id: USER_ID,
    title: "Canadian railways",
    message_count: 6,
    last_message_at: "2026-07-30T19:04:00Z",
    latest_run_id: "2fe46c40-fb7a-4fe8-aa56-a06627963f14",
    created_at: "2026-07-30T18:50:00Z",
    updated_at: "2026-07-30T19:04:00Z",
  },
  {
    id: "f43c3227-f7a3-4b6a-adcb-9a403350d03d",
    user_id: USER_ID,
    title: "Retail margin check",
    message_count: 2,
    last_message_at: "2026-07-28T16:40:00Z",
    latest_run_id: "f18556de-063d-43ad-ac83-00924d9d56b1",
    created_at: "2026-07-28T16:36:00Z",
    updated_at: "2026-07-28T16:40:00Z",
  },
];

const run = {
  id: RUN_ID,
  thread_id: threads[0].id,
  trigger_message_id: "bc4c7956-3331-419d-8bc1-8a4457c48d5e",
  status: "succeeded" as const,
  target_ticker: "AAPL",
  peer_tickers: ["MSFT", "GOOGL", "META"],
  currency: "USD",
  as_of: "2026-07-31T20:00:00Z",
  warnings: [
    "META EV / EBITDA is unavailable because current EBITDA evidence was incomplete.",
    "GOOGL P / E uses the latest available net income filing, dated 12 days before the quote.",
  ],
  error_message: null,
  created_at: "2026-08-01T14:32:08Z",
  started_at: "2026-08-01T14:32:08Z",
  completed_at: "2026-08-01T14:32:14Z",
};

const rows: CompsRow[] = [
  { ticker: "AAPL", company_name: "Apple Inc.", is_target: true, currency: "USD", share_price: 213.05, market_cap: 3180, enterprise_value: 3251, revenue_ltm: 408.6, ebitda_ltm: 140.3, ev_to_revenue: 7.96, ev_to_ebitda: 23.17, pe: 32.4, as_of: run.as_of },
  { ticker: "MSFT", company_name: "Microsoft Corporation", is_target: false, currency: "USD", share_price: 519.84, market_cap: 3864, enterprise_value: 3822, revenue_ltm: 281.7, ebitda_ltm: 166.2, ev_to_revenue: 13.56, ev_to_ebitda: 23.0, pe: 37.1, as_of: run.as_of },
  { ticker: "GOOGL", company_name: "Alphabet Inc.", is_target: false, currency: "USD", share_price: 191.42, market_cap: 2351, enterprise_value: 2267, revenue_ltm: 371.4, ebitda_ltm: 132.5, ev_to_revenue: 6.1, ev_to_ebitda: 17.11, pe: 21.7, as_of: run.as_of },
  { ticker: "META", company_name: "Meta Platforms, Inc.", is_target: false, currency: "USD", share_price: 728.56, market_cap: 1834, enterprise_value: 1810, revenue_ltm: 178.8, ebitda_ltm: null, ev_to_revenue: 10.12, ev_to_ebitda: null, pe: 27.9, as_of: run.as_of },
];

const trace = {
  run_id: RUN_ID,
  formulas: [
    {
      ticker: "AAPL",
      output_field: "enterprise_value",
      expression: "market_cap + total_debt - cash",
      output_value: 3251000000000,
      inputs: [
        { field: "market_cap", value: 3180000000000, source: "Alpha Vantage GLOBAL_QUOTE", as_of: run.as_of },
        { field: "total_debt", value: 119000000000, source: "Alpha Vantage BALANCE_SHEET", as_of: "2026-06-28T00:00:00Z" },
        { field: "cash", value: 48000000000, source: "Alpha Vantage BALANCE_SHEET", as_of: "2026-06-28T00:00:00Z" },
      ],
    },
    {
      ticker: "AAPL",
      output_field: "ev_to_revenue",
      expression: "enterprise_value / revenue_ltm",
      output_value: 7.96,
      inputs: [
        { field: "enterprise_value", value: 3251000000000, source: "Calculated", as_of: run.as_of },
        { field: "revenue_ltm", value: 408600000000, source: "Alpha Vantage INCOME_STATEMENT", as_of: "2026-06-28T00:00:00Z" },
      ],
    },
  ],
};

const takeaways = {
  headline: "Apple trades above the peer median on revenue, but close to the group on EBITDA.",
  body: "AAPL’s 7.96× EV / Revenue is 12% above the 7.03× peer median, while its 23.17× EV / EBITDA is broadly in line with Microsoft and above Alphabet. That gap may reflect Apple’s margins and durability, but the small peer set and one missing EBITDA value limit confidence.",
  confidence: "Moderate confidence",
};

const variantNames: Record<VariantKey, string> = {
  A: "Guided workspace",
  B: "Research desk",
  C: "Narrative canvas",
};

const stateLabels: Record<DemoState, string> = {
  arrival: "First arrival",
  waiting: "Waiting",
  success: "Success + warnings",
  "input-error": "Recoverable input error",
  failed: "Failed Run",
};

function money(value: number | null) {
  if (value === null) return "—";
  return `$${value.toLocaleString()}B`;
}

function multiple(value: number | null) {
  return value === null ? "—" : `${value.toFixed(2)}×`;
}

function validateChatRequest(prompt: string) {
  const request = prompt.trim();
  if (!request) return "Tell me which company you want to compare and name the peers you have in mind.";
  const companyNames = ["apple", "microsoft", "alphabet", "google", "meta", "nvidia", "amazon", "tesla", "coca-cola", "pepsi", "railway", "railways"];
  const namedCompanies = companyNames.filter((name) => request.toLowerCase().includes(name));
  const tickerTokens = request.match(/\b[A-Z.]{2,10}\b/g) ?? [];
  if (new Set([...namedCompanies, ...tickerTokens]).size < 3) return "I can start with Apple and Microsoft. Which other company should join the peer group?";
  return null;
}

function PrototypeBadge() {
  return <div className="prototype-badge">PROTOTYPE · DISPOSABLE</div>;
}

function FutureNav({ compact = false }: { compact?: boolean }) {
  return (
    <div className={compact ? "future-nav compact" : "future-nav"}>
      <button className="nav-active"><span>⌁</span>{!compact && "Comps"}</button>
      <button disabled title="Future analysis area"><span>◫</span>{!compact && "News signals"}<em>Later</em></button>
      <button disabled title="Future analysis area"><span>⌁</span>{!compact && "Technical view"}<em>Later</em></button>
    </div>
  );
}

function WarningList({ quiet = false }: { quiet?: boolean }) {
  return (
    <div className={quiet ? "warnings quiet" : "warnings"}>
      <div className="warning-title"><span>!</span><strong>2 things to keep in mind</strong></div>
      {run.warnings.map((warning) => <p key={warning}>{warning}</p>)}
    </div>
  );
}

function TracePanel({ onClose, mode = "panel" }: { onClose: () => void; mode?: "panel" | "inline" }) {
  return (
    <section className={`trace-panel ${mode}`} aria-label="Trace inspection">
      <div className="trace-head">
        <div><span className="eyebrow">TRACE · RUN {RUN_ID.slice(0, 8)}</span><h2>How the numbers were built</h2></div>
        <button className="icon-button" onClick={onClose} aria-label="Close trace">×</button>
      </div>
      <p className="trace-intro">Every calculated value points back to a formula and the evidence used. This is the product Trace—not an agent log.</p>
      {trace.formulas.map((formula) => (
        <article className="formula" key={formula.output_field}>
          <div className="formula-top"><strong>{formula.ticker} · {formula.output_field.replaceAll("_", " ")}</strong><b>{typeof formula.output_value === "number" && formula.output_value < 100 ? multiple(formula.output_value) : money(3251)}</b></div>
          <code>{formula.expression}</code>
          <div className="inputs">
            {formula.inputs.map((input) => (
              <div key={input.field}><span>{input.field.replaceAll("_", " ")}</span><strong>{typeof input.value === "number" && input.value > 1000000 ? `$${(input.value / 1_000_000_000).toFixed(1)}B` : input.value}</strong><small>{input.source}<br />As of {new Date(input.as_of).toLocaleDateString("en-CA")}</small></div>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}

function FullTable({ selectedMetric = "ev_to_revenue", onMetric }: { selectedMetric?: string; onMetric?: (metric: string) => void }) {
  const metrics = [
    ["ev_to_revenue", "EV / Revenue"],
    ["ev_to_ebitda", "EV / EBITDA"],
    ["pe", "P / E"],
  ];
  return (
    <div className="table-wrap">
      <div className="metric-tabs">
        {metrics.map(([key, label]) => <button key={key} className={selectedMetric === key ? "selected" : ""} onClick={() => onMetric?.(key)}>{label}</button>)}
      </div>
      <table>
        <thead><tr><th>Company</th><th>Share price</th><th>Market cap</th><th>Enterprise value</th><th>Revenue LTM</th><th>EBITDA LTM</th><th>EV / Revenue</th><th>EV / EBITDA</th><th>P / E</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.ticker} className={row.is_target ? "target-row" : ""}>
              <td><strong>{row.ticker}</strong><small>{row.company_name}</small>{row.is_target && <em>Target</em>}</td>
              <td>${row.share_price?.toFixed(2)}</td><td>{money(row.market_cap)}</td><td>{money(row.enterprise_value)}</td><td>{money(row.revenue_ltm)}</td>
              <td className={row.ebitda_ltm === null ? "missing" : ""}>{money(row.ebitda_ltm)}{row.ebitda_ltm === null && <small>Evidence missing</small>}</td>
              <td className={selectedMetric === "ev_to_revenue" ? "metric-focus" : ""}>{multiple(row.ev_to_revenue)}</td>
              <td className={`${selectedMetric === "ev_to_ebitda" ? "metric-focus" : ""} ${row.ev_to_ebitda === null ? "missing" : ""}`}>{multiple(row.ev_to_ebitda)}{row.ev_to_ebitda === null && <small>Not calculated</small>}</td>
              <td className={selectedMetric === "pe" ? "metric-focus" : ""}>{multiple(row.pe)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="table-foot">USD billions except share price and multiples · Latest available evidence as of Jul 31, 2026</p>
    </div>
  );
}

function ChatComposer({ prompt, setPrompt, onSubmit, tone = "light", showSuggestion = false }: {
  prompt: string;
  setPrompt: (value: string) => void;
  onSubmit: () => void;
  tone?: "light" | "dark" | "paper";
  showSuggestion?: boolean;
}) {
  return (
    <div className={`chat-composer ${tone}`}>
      {showSuggestion && <button className="prompt-suggestion" onClick={() => setPrompt(DEFAULT_PROMPT)}>Try: “Compare Apple with Microsoft, Alphabet, and Meta”</button>}
      <div className="composer-box">
        <textarea
          aria-label="Message TalkToYourStock"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder="Ask for a comparison in your own words…"
          rows={3}
        />
        <button className="send-message" onClick={onSubmit} aria-label="Send message">↑</button>
      </div>
      <small>Enter to send · Shift + Enter for a new line</small>
    </div>
  );
}

function ChatTranscript({ state, prompt, error, style = "calm" }: {
  state: DemoState;
  prompt: string;
  error: string | null;
  style?: "calm" | "terminal" | "story";
}) {
  if (state === "arrival") {
    return (
      <div className={`chat-welcome ${style}`}>
        <span className="eyebrow">NEW THREAD</span>
        <h2>What would you like to understand?</h2>
        <p>Ask naturally. Name the company you care about and any peers you want included; I’ll turn the request into an analysis on the canvas.</p>
      </div>
    );
  }

  const assistantMessage = state === "input-error"
    ? error
    : state === "waiting"
      ? "I found the companies. I’m aligning their latest evidence and building the Comps Table now."
      : state === "failed"
        ? "I couldn’t complete this Run, but your request and the failure details are saved in this Thread."
        : "The comparison is ready. I put the table and the main relative-valuation takeaway on the canvas.";

  return (
    <div className={`chat-transcript ${style}`}>
      <div className="chat-message user"><span>YOU</span><p>{prompt || DEFAULT_PROMPT}</p></div>
      <div className={`chat-message assistant ${state === "input-error" ? "clarification" : ""}`}>
        <span>TALKTOYOURSTOCK</span><p>{assistantMessage}</p>
        {state === "input-error" && <small>No Run has been created yet.</small>}
      </div>
    </div>
  );
}

function EmptyAnalysisCanvas({ mode }: { mode: "guided" | "desk" | "story" }) {
  return (
    <div className={`empty-analysis ${mode}`}>
      <div className="artifact-orbit"><i /><i /><i /><span>⌁</span></div>
      <span className="eyebrow">ANALYSIS CANVAS</span>
      <h1>{mode === "story" ? "Your request will become a visual analysis story." : "Your analysis will appear here."}</h1>
      <p>The canvas stays open for the artifact your request needs—starting with a Comps Table, and later other analysis visuals.</p>
      <div className="artifact-types">
        <span className="available"><b>Comps Table</b><small>Available now</small></span>
        <span><b>News signals</b><small>Future analysis</small></span>
        <span><b>Technical view</b><small>Future analysis</small></span>
      </div>
    </div>
  );
}

function WaitingCard({ style = "card" }: { style?: "card" | "bar" | "story" }) {
  return (
    <div className={`waiting ${style}`}>
      <div className="loader"><i /><i /><i /></div>
      <div><span className="eyebrow">RUNNING · AAPL + 3 PEERS</span><h2>Building a comparable view</h2><p>Checking market values, aligning the latest filings, then calculating each multiple.</p></div>
      <ol><li className="done">Tickers validated</li><li className="active">Loading source evidence</li><li>Calculating Comps Table</li><li>Preparing Trace</li></ol>
    </div>
  );
}

function FailedRun({ onRetry, compact = false }: { onRetry: () => void; compact?: boolean }) {
  return (
    <div className={compact ? "failed-run compact" : "failed-run"}>
      <span className="failure-mark">×</span>
      <div><span className="eyebrow">RUN FAILED · {RUN_ID.slice(0, 8)}</span><h2>We couldn’t finish this comparison</h2><p>Alpha Vantage’s request limit was reached while loading AAPL. Your Thread and request are saved.</p><small>No Comps Table was produced, so there is no result to interpret.</small></div>
      <button className="primary" onClick={onRetry}>Try the Run again</button>
    </div>
  );
}

function VariantA(props: SharedVariantProps) {
  const { state, setState, prompt, setPrompt, error, runRequest, traceOpen, setTraceOpen } = props;
  return (
    <main className="variant-a">
      <aside className="a-sidebar">
        <div className="brand"><div className="brand-mark">T<span>+</span></div><div><strong>TalkToYourStock</strong><small>Evidence before opinion</small></div></div>
        <button className="new-thread" onClick={() => setState("arrival")}>＋ New comparison</button>
        <FutureNav />
        <div className="thread-section"><div className="section-label">THREAD HISTORY <button>⌕</button></div>{threads.map((thread, index) => <button className={`thread ${index === 0 && state !== "arrival" ? "active" : ""}`} key={thread.id} onClick={() => setState("success")}><span>{thread.title}</span><small>{index === 0 ? "Today" : `${index + 2}d ago`} · {thread.message_count} Messages</small></button>)}</div>
        <div className="profile"><span>MD</span><div><strong>Mitansh</strong><small>Local workspace</small></div><button>•••</button></div>
      </aside>
      <section className="a-content">
        <header className="a-top"><div><span className="crumb">Comps /</span><strong>{state === "arrival" ? "New comparison" : "Apple vs mega-cap peers"}</strong></div><div className="status-dot">Local fixture data</div></header>
        <div className="a-workbench">
          <aside className="a-chat-panel">
            <div className="chat-panel-head"><span className="eyebrow">THREAD CHAT</span><strong>{state === "arrival" ? "Start with a question" : "Apple vs mega-cap peers"}</strong></div>
            <div className="a-chat-scroll"><ChatTranscript {...{ state, prompt, error }} /></div>
            <ChatComposer {...{ prompt, setPrompt, onSubmit: runRequest }} showSuggestion={state === "arrival"} />
            <p className="recommendation-boundary">Analysis support only—never a buy, sell, or hold recommendation.</p>
          </aside>
          <section className="a-canvas">
            <header className="canvas-bar"><div><span className="eyebrow">MAIN SURFACE</span><strong>{state === "success" ? "Comps analysis" : "Ready for an analysis artifact"}</strong></div><div className="canvas-destinations"><button className="active">Comps</button><button disabled>News · Later</button><button disabled>Technicals · Later</button></div></header>
            {state === "arrival" || state === "input-error" ? <EmptyAnalysisCanvas mode="guided" /> : state === "waiting" ? <div className="a-state-center"><WaitingCard /></div> : state === "failed" ? <div className="a-state-center"><FailedRun onRetry={runRequest} /></div> : (
              <div className="a-success">
                <section className="result-hero"><div><span className="eyebrow">COMPARISON TAKEAWAY</span><h1>{takeaways.headline}</h1><p>{takeaways.body}</p><div className="confidence"><span>◒</span><strong>{takeaways.confidence}</strong><em>Small peer set · one missing metric</em></div></div><div className="hero-multiple"><span>AAPL</span><strong>7.96×</strong><small>EV / Revenue</small><div><i style={{ width: "61%" }} /><b>Peer median 7.03×</b></div></div></section>
                <WarningList quiet />
                <section className="a-table-section"><div className="section-head"><div><span className="eyebrow">COMPS TABLE</span><h2>See the comparison behind the takeaway</h2></div><div><button className="secondary" onClick={() => setTraceOpen(true)}>Inspect Trace</button><button className="secondary" disabled>Export later</button></div></div><FullTable /></section>
              </div>
            )}
          </section>
        </div>
      </section>
      {traceOpen && <div className="trace-overlay"><TracePanel onClose={() => setTraceOpen(false)} /></div>}
    </main>
  );
}

function VariantB(props: SharedVariantProps) {
  const { state, setState, prompt, setPrompt, error, runRequest, traceOpen, setTraceOpen } = props;
  const [metric, setMetric] = useState("ev_to_revenue");
  return (
    <main className="variant-b">
      <header className="b-header"><div className="b-brand"><span>TY</span><strong>TalkToYourStock</strong><em>LAB</em></div><nav><button className="active">Comps</button><button disabled>News <small>Later</small></button><button disabled>Technicals <small>Later</small></button></nav><div className="b-actions"><button>⌘ K</button><button className="avatar">MD</button></div></header>
      <div className="b-threadbar"><strong>Threads</strong>{threads.map((thread, index) => <button key={thread.id} className={index === 0 && state !== "arrival" ? "active" : ""} onClick={() => setState("success")}><i />{thread.title}<small>{index === 0 ? "14:33" : `Jul ${30 - index * 2}`}</small></button>)}<button className="add" onClick={() => setState("arrival")}>＋</button></div>
      <div className="b-body">
        <aside className="b-chat-rail">
          <div className="command-title"><span>›_</span><strong>Analysis chat</strong><kbd>⌘ ↵</kbd></div>
          <div className="b-chat-scroll"><ChatTranscript {...{ state, prompt, error }} style="terminal" /></div>
          <ChatComposer {...{ prompt, setPrompt, onSubmit: runRequest }} tone="dark" showSuggestion={state === "arrival"} />
          <div className="b-chat-meta"><span>Natural-language request</span><span>Fixture only</span></div>
        </aside>
        <section className="b-artifact-area">
          {state === "arrival" || state === "input-error" ? (
            <div className="b-empty-wrap"><EmptyAnalysisCanvas mode="desk" /><div className="recent-runs"><span>RECENT ARTIFACTS</span><div><strong>AAPL / MSFT GOOGL META</strong><em className="success-dot">Comps Table</em><small>6s · Today 14:32</small></div><div><strong>CNR / CP UNP CSX</strong><em className="failure-dot">Failed Run</em><small>Jul 30 19:02</small></div></div></div>
          ) : state === "waiting" ? <div className="b-wait"><WaitingCard style="bar" /><div className="skeleton-grid">{Array.from({ length: 28 }).map((_, index) => <i key={index} />)}</div></div> : state === "failed" ? <div className="b-failed"><FailedRun compact onRetry={runRequest} /><section><span>RUN EVENT</span><code>UPSTREAM_ERROR</code><p>Alpha Vantage request limit was reached while loading AAPL.</p><small>Thread preserved · Run ID {RUN_ID}</small></section></div> : (
            <section className="b-workspace">
              <div className="b-runline"><div><span className="success-dot">SUCCEEDED</span><strong>AAPL</strong><span>vs</span>{run.peer_tickers.map((peer) => <b key={peer}>{peer}</b>)}<small>Latest · USD · 6.2s</small></div><div><button onClick={() => setPrompt(DEFAULT_PROMPT)}>Refine in chat</button><button onClick={() => setTraceOpen(!traceOpen)}>⌁ {traceOpen ? "Hide" : "Open"} Trace</button></div></div>
              <div className="b-grid">
                <section className="b-table"><FullTable selectedMetric={metric} onMetric={setMetric} /></section>
                <aside className="b-inspector"><span className="eyebrow">READOUT</span><h2>{takeaways.headline}</h2><p>{takeaways.body}</p><div className="readout-stat"><span>Target premium</span><strong>+12%</strong><small>vs peer median EV / Revenue</small></div><WarningList /><div className="method"><strong>Interpretation boundary</strong><p>Relative valuation is context, not a recommendation. Price, quality, growth, and risk can move together.</p></div></aside>
              </div>
              {traceOpen && <div className="b-trace-drawer"><TracePanel mode="inline" onClose={() => setTraceOpen(false)} /></div>}
            </section>
          )}
        </section>
      </div>
    </main>
  );
}

function VariantC(props: SharedVariantProps) {
  const { state, setState, prompt, setPrompt, error, runRequest, setTraceOpen } = props;
  const [chapter, setChapter] = useState<"takeaway" | "table" | "trace">("takeaway");
  return (
    <main className="variant-c">
      <aside className="c-rail"><div className="c-logo">t<span>+</span></div><FutureNav compact /><div className="rail-bottom"><button>?</button><button className="avatar">MD</button></div></aside>
      <section className="c-conversation">
        <header><div><span>Thread</span><strong>{state === "arrival" ? "Untitled comparison" : "Apple vs mega-cap peers"}</strong></div><button className="icon-button" onClick={() => setState("arrival")}>＋</button></header>
        <div className="c-thread-list"><span className="eyebrow">YOUR THREADS</span>{threads.map((thread, index) => <button key={thread.id} className={index === 0 && state !== "arrival" ? "active" : ""} onClick={() => setState("success")}><i>{thread.title.slice(0, 1)}</i><span>{thread.title}<small>{thread.message_count} Messages · {index === 0 ? "Today" : `${index + 2}d`}</small></span></button>)}</div>
        <div className="c-chat"><ChatTranscript {...{ state, prompt, error }} style="story" /></div>
        <div className="c-composer"><ChatComposer {...{ prompt, setPrompt, onSubmit: runRequest }} tone="paper" showSuggestion={state === "arrival"} /><small>TalkToYourStock provides analysis support, not buy, sell, or hold recommendations.</small></div>
      </section>
      <section className="c-canvas">
        <header className="c-canvas-head"><div><span className="eyebrow">ANALYSIS STORY</span><strong>{state === "arrival" ? "Nothing on the canvas yet" : "AAPL relative valuation"}</strong></div>{state === "success" && <div className="chapter-nav"><button className={chapter === "takeaway" ? "active" : ""} onClick={() => setChapter("takeaway")}>01 Takeaway</button><button className={chapter === "table" ? "active" : ""} onClick={() => setChapter("table")}>02 Evidence</button><button className={chapter === "trace" ? "active" : ""} onClick={() => setChapter("trace")}>03 Trace</button></div>}</header>
        {state === "arrival" || state === "input-error" ? <EmptyAnalysisCanvas mode="story" /> : state === "waiting" ? <WaitingCard style="story" /> : state === "failed" ? <FailedRun onRetry={runRequest} /> : chapter === "takeaway" ? (
          <article className="story-takeaway"><div className="story-number">01</div><span className="eyebrow">COMPARISON TAKEAWAY</span><h1>{takeaways.headline}</h1><p>{takeaways.body}</p><div className="story-chart"><div><span>AAPL</span><i style={{ width: "80%" }} /><strong>7.96×</strong></div><div><span>Peer median</span><i style={{ width: "70%" }} /><strong>7.03×</strong></div><small>EV / Revenue</small></div><div className="confidence-story"><strong>{takeaways.confidence}</strong><span>Because the group is small and META is missing one core metric.</span></div><button className="story-next" onClick={() => setChapter("table")}>See the evidence <span>→</span></button></article>
        ) : chapter === "table" ? <article className="story-table"><div className="story-number">02</div><div className="story-heading"><div><span className="eyebrow">COMPS TABLE</span><h1>The evidence in one view</h1></div><button onClick={() => setChapter("trace")}>How was this calculated? →</button></div><WarningList quiet /><FullTable /></article> : <TracePanel mode="inline" onClose={() => { setTraceOpen(false); setChapter("table"); }} />}
      </section>
    </main>
  );
}

type SharedVariantProps = {
  state: DemoState;
  setState: (state: DemoState) => void;
  prompt: string;
  setPrompt: (value: string) => void;
  error: string | null;
  runRequest: () => void;
  traceOpen: boolean;
  setTraceOpen: (open: boolean) => void;
};

function PrototypeSwitcher({ variant, state, onVariant, onState }: { variant: VariantKey; state: DemoState; onVariant: (variant: VariantKey) => void; onState: (state: DemoState) => void }) {
  const variants: VariantKey[] = ["A", "B", "C"];
  const cycle = useCallback((direction: -1 | 1) => {
    const current = variants.indexOf(variant);
    onVariant(variants[(current + direction + variants.length) % variants.length]);
  }, [variant, onVariant]);

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable) return;
      if (event.key === "ArrowLeft") cycle(-1);
      if (event.key === "ArrowRight") cycle(1);
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, [cycle]);

  if (process.env.NODE_ENV === "production") return null;
  return (
    <div className="prototype-switcher">
      <span className="switcher-proto">PROTOTYPE</span>
      <button onClick={() => cycle(-1)} aria-label="Previous variant">←</button>
      <div className="variant-choice"><strong>{variant}</strong><span>{variantNames[variant]}</span></div>
      <button onClick={() => cycle(1)} aria-label="Next variant">→</button>
      <div className="switcher-divider" />
      <label>Preview state<select value={state} onChange={(event) => onState(event.target.value as DemoState)}>{(Object.keys(stateLabels) as DemoState[]).map((key) => <option key={key} value={key}>{stateLabels[key]}</option>)}</select></label>
      <div className="variant-pills">{variants.map((key) => <button key={key} className={key === variant ? "active" : ""} onClick={() => onVariant(key)}>{key}</button>)}</div>
    </div>
  );
}

function PrototypePageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const param = searchParams.get("variant")?.toUpperCase();
  const variant: VariantKey = param === "B" || param === "C" ? param : "A";
  const initialState = searchParams.get("state") as DemoState | null;
  const [state, setStateValue] = useState<DemoState>(initialState && initialState in stateLabels ? initialState : "arrival");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);

  const setVariant = useCallback((next: VariantKey) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("variant", next);
    router.replace(`/?${params.toString()}`);
  }, [router, searchParams]);

  const setState = useCallback((next: DemoState) => {
    setStateValue(next);
    setTraceOpen(false);
    setError(next === "input-error" ? "I can start with Apple and Microsoft. Which other company should join the peer group?" : null);
    if (next === "input-error") setPrompt(INCOMPLETE_PROMPT);
    if (next === "arrival") setPrompt("");
    if (["waiting", "success", "failed"].includes(next)) setPrompt(DEFAULT_PROMPT);
  }, []);

  const runRequest = useCallback(() => {
    const validationError = validateChatRequest(prompt);
    if (validationError) { setError(validationError); setStateValue("input-error"); return; }
    setError(null);
    setStateValue("waiting");
    window.setTimeout(() => setStateValue("success"), 1500);
  }, [prompt]);

  const props = useMemo<SharedVariantProps>(() => ({ state, setState, prompt, setPrompt, error, runRequest, traceOpen, setTraceOpen }), [state, setState, prompt, error, runRequest, traceOpen]);

  return (
    <>
      <PrototypeBadge />
      {variant === "A" && <VariantA {...props} />}
      {variant === "B" && <VariantB {...props} />}
      {variant === "C" && <VariantC {...props} />}
      <PrototypeSwitcher variant={variant} state={state} onVariant={setVariant} onState={setState} />
    </>
  );
}

export default function PrototypePage() {
  return (
    <Suspense fallback={<div className="prototype-loading">Loading prototype…</div>}>
      <PrototypePageContent />
    </Suspense>
  );
}
