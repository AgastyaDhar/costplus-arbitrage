// Cost Plus Drugs -- internal war-room dashboard.
//
// PATH ASSUMPTION: this file is served from dashboard/ at the repo root. It fetches
//   ../costplus_suite/output/leaderboard.csv        (costplus_suite/output/leaderboard.csv)
//   ../costplus_suite/output/spread_changes.csv     (costplus_suite/output/spread_changes.csv)
//   ../data/trumprx.csv                             (data/trumprx.csv, repo root)
// Adjust the three paths below if this file is relocated. data/trumprx.csv does not
// exist yet in this repo (Module E has never been run against real data) -- Panel 4
// renders its "missing" state until it does.
//
// NADAC_SNAPSHOT_DATE and CATALOG_COVERAGE_PCT below are build-time constants, not
// derived from the three CSVs this dashboard fetches -- leaderboard.csv carries no
// date field, and catalog size lives in data/costplus.GRAPHQL.csv, which this
// dashboard never loads. Sourced from costplus_suite/cache/resolved_ids.json
// (nadac.snapshot_date) and a manual row count at time of writing. On a real weekly
// refresh cadence these two need their own small provenance file or CSV column --
// they will NOT move on their own the way the leaderboard total does.

import React, { useEffect, useMemo, useRef, useState } from "react";
import Papa from "papaparse";

const NADAC_SNAPSHOT_DATE = "2026-07-08";
const CATALOG_COVERAGE_PCT = 84.8; // 2,024 / 2,386 scraped catalog rows
const LEADERBOARD_URL = "../costplus_suite/output/leaderboard.csv";
const SPREAD_URL = "../costplus_suite/output/spread_changes.csv";
const TRUMPRX_URL = "../data/trumprx.csv";

const fmtB = (n) => `$${(n / 1e9).toFixed(1)}B`;
const fmtUSD = (n) => `$${Math.round(n).toLocaleString("en-US")}`;
const fmtUnit = (n) => (n == null || Number.isNaN(n) ? "--" : `$${Number(n).toFixed(4)}`);
const fmtPct = (n) => (n == null || Number.isNaN(n) ? "--" : `${Number(n).toFixed(1)}%`);

function useCountUp(target, durationMs = 1500) {
  const [value, setValue] = useState(0);
  const startRef = useRef(null);
  useEffect(() => {
    if (!target) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (reduced) { setValue(target); return; }
    let raf;
    const ease = (t) => 1 - Math.pow(1 - t, 3);
    const step = (ts) => {
      if (startRef.current === null) startRef.current = ts;
      const t = Math.min((ts - startRef.current) / durationMs, 1);
      setValue(target * ease(t));
      if (t < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);
  return value;
}

function loadCsv(url, onDone, onMissing) {
  Papa.parse(url, {
    download: true,
    header: true,
    dynamicTyping: true,
    skipEmptyLines: true,
    complete: (res) => {
      const rows = (res.data || []).filter((r) => Object.values(r).some((v) => v !== null && v !== ""));
      if (rows.length === 0) onMissing(); else onDone(rows);
    },
    error: () => onMissing(),
  });
}

function StatChip({ label, value }) {
  return (
    <div className="border border-[#1b2436] px-4 py-2">
      <div className="text-[10px] uppercase tracking-widest text-[#5f6b85]">{label}</div>
      <div className="font-mono tabular-nums text-lg text-[#e8ebf2]">{value}</div>
    </div>
  );
}

function Headline({ total, isLoading }) {
  const animated = useCountUp(isLoading ? 0 : total);
  return (
    <section className="col-span-full border border-[#1b2436] bg-[#0d1424] p-6">
      <div className="text-xs uppercase tracking-widest text-[#5f6b85]">
        Estimated Medicare + Medicaid overpayment vs Cost Plus prices (generics only)
      </div>
      <div className="font-mono tabular-nums text-[64px] leading-none text-[#00ff88] mt-2 md:text-[88px]">
        {isLoading ? "$0.0B" : fmtB(animated)}
      </div>
      <div className="mt-3 flex flex-wrap gap-3">
        <StatChip label="Drugs analyzed" value="2,024" />
        <StatChip label="Catalog coverage" value={fmtPct(CATALOG_COVERAGE_PCT)} />
        <StatChip label="Data refreshed" value={NADAC_SNAPSHOT_DATE} />
      </div>
      <div className="mt-3 text-xs text-[#5f6b85]">
        Built on NADAC, Medicare Part D, and Medicaid SDUD. Net prices never estimated.
      </div>
      <div className="mt-1 text-xs text-[#5f6b85]">Generics only &mdash; rebates on brands excluded.</div>
    </section>
  );
}

const COLUMNS = [
  { key: "rank", label: "Rank" },
  { key: "drug_term", label: "Drug" },
  { key: "costplus_per_unit", label: "Cost Plus/unit" },
  { key: "partd_per_unit", label: "System/unit", tooltip: "Gross Medicare Part D spend per dosage unit. Does not reflect net-of-rebate prices." },
  { key: "gap_partd", label: "Gap" },
  { key: "total_overpayment", label: "Total Overpayment" },
];

function LeaderboardPanel({ rows, status }) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState("total_overpayment");
  const [sortDir, setSortDir] = useState("desc");
  const [expandedRank, setExpandedRank] = useState(null);

  const maxOverpayment = useMemo(
    () => rows.reduce((m, r) => Math.max(m, r.total_overpayment || 0), 0) || 1,
    [rows]
  );

  const view = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q ? rows.filter((r) => String(r.drug_term).toLowerCase().includes(q)) : rows;
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      if (typeof av === "string") return av.localeCompare(bv) * dir;
      return ((av ?? 0) - (bv ?? 0)) * dir;
    });
  }, [rows, query, sortKey, sortDir]);

  const toggleSort = (key) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("desc"); }
  };

  const exportCsv = () => {
    const csv = Papa.unparse(view.map((r) => ({
      rank: r.rank, drug: r.drug_term, costplus_per_unit: r.costplus_per_unit,
      system_per_unit: r.partd_per_unit, gap: r.gap_partd, total_overpayment: r.total_overpayment,
    })));
    const blob = new Blob([csv], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "costplus_leaderboard_top25.csv";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <section className="border border-[#1b2436] bg-[#0d1424] p-4 flex flex-col min-h-0">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h2 className="text-xs uppercase tracking-widest text-[#5f6b85]">Overpayment Leaderboard &mdash; Top 25</h2>
        <button
          onClick={exportCsv}
          className="border border-[#1b2436] px-3 py-1 text-xs uppercase tracking-widest text-[#e8ebf2] hover:border-[#00ff88] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#00ff88]"
        >
          Export
        </button>
      </div>
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Filter by drug name..."
        className="mb-3 bg-transparent border border-[#1b2436] px-3 py-2 text-sm text-[#e8ebf2] placeholder-[#5f6b85] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[#00ff88]"
      />
      {status === "loading" && <div className="text-sm text-[#5f6b85]">Loading leaderboard...</div>}
      {status === "error" && <div className="text-sm text-[#ff6b4a]">Failed to load output/leaderboard.csv</div>}
      {status === "ready" && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-[#1b2436] text-left text-[10px] uppercase tracking-widest text-[#5f6b85]">
                {COLUMNS.map((c) => (
                  <th key={c.key} title={c.tooltip} className="py-2 pr-3 cursor-pointer select-none whitespace-nowrap" onClick={() => toggleSort(c.key)}>
                    {c.label}{sortKey === c.key ? (sortDir === "asc" ? " ↑" : " ↓") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.map((r) => {
                const opacity = 0.08 + 0.55 * (Math.max(r.total_overpayment, 0) / maxOverpayment);
                const isOpen = expandedRank === r.rank;
                return (
                  <React.Fragment key={r.rank}>
                    <tr
                      onClick={() => setExpandedRank(isOpen ? null : r.rank)}
                      className="border-b border-[#151b2a] cursor-pointer hover:bg-[#111a2c] font-mono tabular-nums"
                    >
                      <td className="py-2 pr-3 text-[#5f6b85]">{r.rank}</td>
                      <td className="py-2 pr-3 font-sans text-[#e8ebf2] whitespace-nowrap">{r.drug_term}</td>
                      <td className="py-2 pr-3 text-[#e8ebf2]">{fmtUnit(r.costplus_per_unit)}</td>
                      <td className="py-2 pr-3 text-[#e8ebf2]">{fmtUnit(r.partd_per_unit)}</td>
                      <td className="py-2 pr-3 text-[#e8ebf2]">{fmtUnit(r.gap_partd)}</td>
                      <td className="py-2 pr-3 text-[#e8ebf2]" style={{ backgroundColor: `rgba(0,255,136,${opacity})` }}>
                        {fmtUSD(r.total_overpayment)}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="border-b border-[#151b2a] bg-[#0a0e1a]">
                        <td colSpan={COLUMNS.length} className="py-3 px-3 font-mono tabular-nums text-xs text-[#e8ebf2]">
                          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            <div><span className="text-[#5f6b85] font-sans">NADAC acquisition cost </span>{fmtUnit(r.nadac_per_unit)}</div>
                            <div><span className="text-[#5f6b85] font-sans">Part D gap </span>{fmtUnit(r.gap_partd)} ({fmtUSD(r.overpayment_partd)})</div>
                            <div><span className="text-[#5f6b85] font-sans">Medicaid gap </span>{fmtUnit(r.gap_medicaid)} ({fmtUSD(r.overpayment_medicaid)})</div>
                            <div><span className="text-[#5f6b85] font-sans">Canonical unit </span>{r.canonical_unit}</div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SpreadPanel({ rows, status }) {
  return (
    <section className="border border-[#1b2436] bg-[#0d1424] p-4">
      <h2 className="text-xs uppercase tracking-widest text-[#5f6b85] mb-3">Spread Alert &mdash; widening vs NADAC</h2>
      {status !== "ready" ? (
        <div className="text-sm text-[#5f6b85]">Spread tracker activates after second weekly NADAC pull.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse font-mono tabular-nums">
            <thead>
              <tr className="border-b border-[#1b2436] text-left text-[10px] uppercase tracking-widest text-[#5f6b85]">
                <th className="py-2 pr-3">Drug</th><th className="py-2 pr-3">Last week</th><th className="py-2 pr-3">This week</th><th className="py-2 pr-3">Change</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 15).map((r) => (
                <tr key={r.drug_term} className="border-b border-[#151b2a]">
                  <td className="py-2 pr-3 font-sans text-[#e8ebf2]">{r.drug_term}</td>
                  <td className="py-2 pr-3 text-[#e8ebf2]">{fmtUnit(r.gap_nadac_previous)}</td>
                  <td className="py-2 pr-3 text-[#e8ebf2]">{fmtUnit(r.gap_nadac_current)}</td>
                  <td className="py-2 pr-3" style={{ color: r.widening ? "#ff6b4a" : "#5f6b85" }}>
                    {r.widening ? "↑" : "↓"} {fmtUnit(Math.abs(r.gap_change))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function TrumpRxPanel({ rows, status }) {
  const sorted = useMemo(() => [...rows].sort((a, b) => (b.gap || 0) - (a.gap || 0)), [rows]);
  return (
    <section className="border border-[#1b2436] bg-[#0d1424] p-4">
      <h2 className="text-xs uppercase tracking-widest text-[#5f6b85] mb-3">Brand on TrumpRx vs generic at Cost Plus</h2>
      {status !== "ready" ? (
        <div className="text-sm text-[#5f6b85]">data/trumprx.csv not available yet &mdash; brand-vs-generic exhibit pending a completed Module E run.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse font-mono tabular-nums">
            <thead>
              <tr className="border-b border-[#1b2436] text-left text-[10px] uppercase tracking-widest text-[#5f6b85]">
                <th className="py-2 pr-3">Brand</th><th className="py-2 pr-3">TrumpRx</th><th className="py-2 pr-3">Cost Plus generic</th><th className="py-2 pr-3">Gap</th><th className="py-2 pr-3">Gap %</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.brand_name} className="border-b border-[#151b2a]">
                  <td className="py-2 pr-3 font-sans text-[#e8ebf2]">{r.brand_name}</td>
                  <td className="py-2 pr-3 text-[#e8ebf2]">${Number(r.trumprx_price).toFixed(2)}</td>
                  <td className="py-2 pr-3 text-[#e8ebf2]">${Number(r.costplus_generic_price).toFixed(2)}</td>
                  <td className="py-2 pr-3 text-[#e8ebf2]">${Number(r.gap).toFixed(2)}</td>
                  <td className="py-2 pr-3 text-[#e8ebf2]">{fmtPct(r.gap_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function CostPlusDashboard() {
  const [leaderboard, setLeaderboard] = useState([]);
  const [totalOverpayment, setTotalOverpayment] = useState(0);
  const [spread, setSpread] = useState([]);
  const [trumprx, setTrumprx] = useState([]);
  const [status, setStatus] = useState({ leaderboard: "loading", spread: "loading", trumprx: "loading" });

  useEffect(() => {
    // The headline aggregate is computed from ALL rows in leaderboard.csv (2,024+),
    // matching modules/a_arbitrage.py's own formula: sum of positive-gap
    // overpayment_partd + sum of positive-gap overpayment_medicaid. The on-screen
    // table below only ever shows the top 25 -- the two must not share one array,
    // or a weekly refresh with a fatter tail would silently understate the headline.
    loadCsv(LEADERBOARD_URL,
      (allRows) => {
        const partdPos = allRows.reduce((s, r) => s + (r.overpayment_partd > 0 ? r.overpayment_partd : 0), 0);
        const medicaidPos = allRows.reduce((s, r) => s + (r.overpayment_medicaid > 0 ? r.overpayment_medicaid : 0), 0);
        setTotalOverpayment(partdPos + medicaidPos);
        setLeaderboard([...allRows].sort((a, b) => a.rank - b.rank).slice(0, 25));
        setStatus((s) => ({ ...s, leaderboard: "ready" }));
      },
      () => setStatus((s) => ({ ...s, leaderboard: "error" })));
    loadCsv(SPREAD_URL,
      (rows) => { setSpread(rows.sort((a, b) => Math.abs(b.gap_change || 0) - Math.abs(a.gap_change || 0))); setStatus((s) => ({ ...s, spread: "ready" })); },
      () => setStatus((s) => ({ ...s, spread: "pending" })));
    loadCsv(TRUMPRX_URL,
      (rows) => { setTrumprx(rows); setStatus((s) => ({ ...s, trumprx: "ready" })); },
      () => setStatus((s) => ({ ...s, trumprx: "missing" })));
  }, []);

  return (
    <div className="min-h-screen bg-[#0a0e1a] text-[#e8ebf2] font-sans p-4 grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4 auto-rows-min">
      <Headline total={totalOverpayment} isLoading={status.leaderboard === "loading"} />
      <LeaderboardPanel rows={leaderboard} status={status.leaderboard} />
      <div className="grid grid-cols-1 gap-4">
        <SpreadPanel rows={spread} status={status.spread} />
        <TrumpRxPanel rows={trumprx} status={status.trumprx} />
      </div>
    </div>
  );
}
