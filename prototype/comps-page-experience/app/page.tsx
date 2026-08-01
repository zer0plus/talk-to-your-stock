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

function validateRequest(target: string, peers: string) {
  const normalizedTarget = target.trim().toUpperCase();
  const peerList = peers.split(/[ ,]+/).map((peer) => peer.trim().toUpperCase()).filter(Boolean);
  if (!normalizedTarget) return "Add a Target Ticker to continue.";
  if (!/^[A-Z.]{1,10}$/.test(normalizedTarget)) return "Use a supported company Ticker, such as AAPL.";
  if (peerList.length < 2) return "Add at least two Peer Tickers so the comparison has context.";
  if (peerList.includes(normalizedTarget)) return "The Target Ticker should not also appear in the peer group.";
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

function RequestFields({ target, peers, setTarget, setPeers, error, onSubmit, compact = false }: {
  target: string; peers: string; setTarget: (value: string) => void; setPeers: (value: string) => void; error: string | null; onSubmit: () => void; compact?: boolean;
}) {
  return (
    <div className={compact ? "request-fields compact" : "request-fields"}>
      <label><span>Target Ticker</span><input value={target} onChange={(event) => setTarget(event.target.value)} placeholder="e.g. AAPL" /></label>
      <label><span>Peer Tickers <small>Explicit peers</small></span><input value={peers} onChange={(event) => setPeers(event.target.value)} placeholder="e.g. MSFT, GOOGL, META" /></label>
      <button className="primary" onClick={onSubmit}>Compare <span>→</span></button>
      {error && <div className="field-error"><strong>Check the peer group</strong><span>{error}</span></div>}
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
  const { state, setState, target, peers, setTarget, setPeers, error, runRequest, traceOpen, setTraceOpen } = props;
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
        {state === "arrival" || state === "input-error" ? (
          <div className="a-arrival">
            <div className="arrival-copy"><span className="eyebrow">GUIDED COMPS WORKSPACE</span><h1>Compare valuation<br />without guessing what matters.</h1><p>Start with one Target Ticker and a peer group you choose. We’ll organize the evidence and explain the differences—without issuing a buy, sell, or hold recommendation.</p></div>
            <div className="guided-card"><div className="step-number">01</div><h2>Choose the companies</h2><p>You can change this peer group and run another comparison inside the same Thread.</p><RequestFields {...{ target, peers, setTarget, setPeers, error, onSubmit: runRequest }} /><div className="example"><span>Try an example</span><button onClick={() => { setTarget("CNR"); setPeers("CP, UNP, CSX"); }}>CNR vs North American railways</button></div></div>
            <div className="learning-strip"><strong>New to Comps?</strong><span>Enterprise value lets you compare the operating value of companies with different debt and cash levels.</span><button>Why these metrics?</button></div>
          </div>
        ) : state === "waiting" ? <div className="a-state-center"><WaitingCard /></div> : state === "failed" ? <div className="a-state-center"><FailedRun onRetry={runRequest} /></div> : (
          <div className="a-success">
            <section className="result-hero"><div><span className="eyebrow">COMPARISON TAKEAWAY</span><h1>{takeaways.headline}</h1><p>{takeaways.body}</p><div className="confidence"><span>◒</span><strong>{takeaways.confidence}</strong><em>Small peer set · one missing metric</em></div></div><div className="hero-multiple"><span>AAPL</span><strong>7.96×</strong><small>EV / Revenue</small><div><i style={{ width: "61%" }} /><b>Peer median 7.03×</b></div></div></section>
            <WarningList quiet />
            <section className="a-table-section"><div className="section-head"><div><span className="eyebrow">COMPS TABLE</span><h2>See the comparison behind the takeaway</h2></div><div><button className="secondary" onClick={() => setTraceOpen(true)}>Inspect Trace</button><button className="secondary" disabled>Export later</button></div></div><FullTable /></section>
          </div>
        )}
      </section>
      {traceOpen && <div className="trace-overlay"><TracePanel onClose={() => setTraceOpen(false)} /></div>}
    </main>
  );
}

function VariantB(props: SharedVariantProps) {
  const { state, setState, target, peers, setTarget, setPeers, error, runRequest, traceOpen, setTraceOpen } = props;
  const [metric, setMetric] = useState("ev_to_revenue");
  return (
    <main className="variant-b">
      <header className="b-header"><div className="b-brand"><span>TY</span><strong>TalkToYourStock</strong><em>LAB</em></div><nav><button className="active">Comps</button><button disabled>News <small>Later</small></button><button disabled>Technicals <small>Later</small></button></nav><div className="b-actions"><button>⌘ K</button><button className="avatar">MD</button></div></header>
      <div className="b-threadbar"><strong>Threads</strong>{threads.map((thread, index) => <button key={thread.id} className={index === 0 && state !== "arrival" ? "active" : ""} onClick={() => setState("success")}><i />{thread.title}<small>{index === 0 ? "14:33" : `Jul ${30 - index * 2}`}</small></button>)}<button className="add" onClick={() => setState("arrival")}>＋</button></div>
      {state === "arrival" || state === "input-error" ? (
        <section className="b-command-arrival"><div className="b-intro"><span className="terminal-label">COMPS DESK / NEW RUN</span><h1>Put a peer group<br />on the desk.</h1><p>A fast, evidence-forward workspace for comparing the valuation of one company against peers you specify.</p></div><div className="command-box"><div className="command-title"><span>⌁</span><strong>Build Comps Table</strong><kbd>⌘ ↵</kbd></div><RequestFields compact {...{ target, peers, setTarget, setPeers, error, onSubmit: runRequest }} /><footer><span>Latest period</span><span>USD</span><span>User-supplied peers</span></footer></div><div className="recent-runs"><span>RECENT RUNS</span><div><strong>AAPL / MSFT GOOGL META</strong><em className="success-dot">Succeeded</em><small>6s · Today 14:32</small></div><div><strong>CNR / CP UNP CSX</strong><em className="failure-dot">Failed</em><small>Jul 30 19:02</small></div></div></section>
      ) : state === "waiting" ? <div className="b-wait"><WaitingCard style="bar" /><div className="skeleton-grid">{Array.from({ length: 28 }).map((_, index) => <i key={index} />)}</div></div> : state === "failed" ? <div className="b-failed"><FailedRun compact onRetry={runRequest} /><section><span>RUN EVENT</span><code>UPSTREAM_ERROR</code><p>Alpha Vantage request limit was reached while loading AAPL.</p><small>Thread preserved · Run ID {RUN_ID}</small></section></div> : (
        <section className="b-workspace">
          <div className="b-runline"><div><span className="success-dot">SUCCEEDED</span><strong>AAPL</strong><span>vs</span>{run.peer_tickers.map((peer) => <b key={peer}>{peer}</b>)}<small>Latest · USD · 6.2s</small></div><div><button onClick={() => setState("arrival")}>Edit peer group</button><button onClick={() => setTraceOpen(!traceOpen)}>⌁ {traceOpen ? "Hide" : "Open"} Trace</button></div></div>
          <div className="b-grid">
            <section className="b-table"><FullTable selectedMetric={metric} onMetric={setMetric} /></section>
            <aside className="b-inspector"><span className="eyebrow">READOUT</span><h2>{takeaways.headline}</h2><p>{takeaways.body}</p><div className="readout-stat"><span>Target premium</span><strong>+12%</strong><small>vs peer median EV / Revenue</small></div><WarningList /><div className="method"><strong>Interpretation boundary</strong><p>Relative valuation is context, not a recommendation. Price, quality, growth, and risk can move together.</p></div></aside>
          </div>
          {traceOpen && <div className="b-trace-drawer"><TracePanel mode="inline" onClose={() => setTraceOpen(false)} /></div>}
        </section>
      )}
    </main>
  );
}

function VariantC(props: SharedVariantProps) {
  const { state, setState, target, peers, setTarget, setPeers, error, runRequest, traceOpen, setTraceOpen } = props;
  const [chapter, setChapter] = useState<"takeaway" | "table" | "trace">("takeaway");
  return (
    <main className="variant-c">
      <aside className="c-rail"><div className="c-logo">t<span>+</span></div><FutureNav compact /><div className="rail-bottom"><button>?</button><button className="avatar">MD</button></div></aside>
      <section className="c-conversation">
        <header><div><span>Thread</span><strong>{state === "arrival" ? "Untitled comparison" : "Apple vs mega-cap peers"}</strong></div><button className="icon-button" onClick={() => setState("arrival")}>＋</button></header>
        <div className="c-thread-list"><span className="eyebrow">YOUR THREADS</span>{threads.map((thread, index) => <button key={thread.id} className={index === 0 && state !== "arrival" ? "active" : ""} onClick={() => setState("success")}><i>{thread.title.slice(0, 1)}</i><span>{thread.title}<small>{thread.message_count} Messages · {index === 0 ? "Today" : `${index + 2}d`}</small></span></button>)}</div>
        <div className="c-chat">
          {state === "arrival" || state === "input-error" ? <div className="c-welcome"><span className="eyebrow">START A THREAD</span><h1>What would you like to compare?</h1><p>Name a Target Ticker and the peers you want beside it. The analysis will unfold as a story you can audit.</p></div> : <><div className="message user"><span>YOU</span><p>Compare Apple with Microsoft, Alphabet, and Meta.</p></div><div className="message assistant"><span>TALKTOYOURSTOCK</span><p>{state === "waiting" ? "I’m gathering the latest evidence and aligning the peer group now." : state === "failed" ? "I couldn’t complete this Run, but I saved your request and the failure details." : "I built the Comps Table. The clearest difference is in revenue valuation; open the analysis story to see why."}</p></div></>}
        </div>
        <div className="c-composer"><RequestFields compact {...{ target, peers, setTarget, setPeers, error, onSubmit: runRequest }} /><small>TalkToYourStock provides analysis support, not buy, sell, or hold recommendations.</small></div>
      </section>
      <section className="c-canvas">
        <header className="c-canvas-head"><div><span className="eyebrow">ANALYSIS STORY</span><strong>{state === "arrival" ? "Nothing on the canvas yet" : "AAPL relative valuation"}</strong></div>{state === "success" && <div className="chapter-nav"><button className={chapter === "takeaway" ? "active" : ""} onClick={() => setChapter("takeaway")}>01 Takeaway</button><button className={chapter === "table" ? "active" : ""} onClick={() => setChapter("table")}>02 Evidence</button><button className={chapter === "trace" ? "active" : ""} onClick={() => setChapter("trace")}>03 Trace</button></div>}</header>
        {state === "arrival" || state === "input-error" ? <div className="empty-canvas"><div className="orbit"><i /><i /><i /><span>t+</span></div><h2>Your comparison will become a three-part story.</h2><div><span><b>01</b>Plain-language takeaway</span><span><b>02</b>Comps Table evidence</span><span><b>03</b>Calculation Trace</span></div></div> : state === "waiting" ? <WaitingCard style="story" /> : state === "failed" ? <FailedRun onRetry={runRequest} /> : chapter === "takeaway" ? (
          <article className="story-takeaway"><div className="story-number">01</div><span className="eyebrow">COMPARISON TAKEAWAY</span><h1>{takeaways.headline}</h1><p>{takeaways.body}</p><div className="story-chart"><div><span>AAPL</span><i style={{ width: "80%" }} /><strong>7.96×</strong></div><div><span>Peer median</span><i style={{ width: "70%" }} /><strong>7.03×</strong></div><small>EV / Revenue</small></div><div className="confidence-story"><strong>{takeaways.confidence}</strong><span>Because the group is small and META is missing one core metric.</span></div><button className="story-next" onClick={() => setChapter("table")}>See the evidence <span>→</span></button></article>
        ) : chapter === "table" ? <article className="story-table"><div className="story-number">02</div><div className="story-heading"><div><span className="eyebrow">COMPS TABLE</span><h1>The evidence in one view</h1></div><button onClick={() => setChapter("trace")}>How was this calculated? →</button></div><WarningList quiet /><FullTable /></article> : <TracePanel mode="inline" onClose={() => { setTraceOpen(false); setChapter("table"); }} />}
      </section>
    </main>
  );
}

type SharedVariantProps = {
  state: DemoState;
  setState: (state: DemoState) => void;
  target: string;
  peers: string;
  setTarget: (value: string) => void;
  setPeers: (value: string) => void;
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
  const [target, setTarget] = useState("AAPL");
  const [peers, setPeers] = useState("MSFT, GOOGL, META");
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
    setError(next === "input-error" ? "Add at least two Peer Tickers so the comparison has context." : null);
    if (next === "input-error") setPeers("MSFT");
    if (next === "arrival") { setTarget("AAPL"); setPeers("MSFT, GOOGL, META"); }
  }, []);

  const runRequest = useCallback(() => {
    const validationError = validateRequest(target, peers);
    if (validationError) { setError(validationError); setStateValue("input-error"); return; }
    setError(null);
    setStateValue("waiting");
    window.setTimeout(() => setStateValue("success"), 1500);
  }, [target, peers]);

  const props = useMemo<SharedVariantProps>(() => ({ state, setState, target, peers, setTarget, setPeers, error, runRequest, traceOpen, setTraceOpen }), [state, setState, target, peers, error, runRequest, traceOpen]);

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
