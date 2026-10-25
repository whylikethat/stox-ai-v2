## 2.4.2

- Tighten the alpha ranker's VALIDATED label. A horizon must now beat random selection from the same universe (positive edge, t >= 1) AND the plain 120-day momentum baseline on the purged holdout. In a live report momentum beat the ranker at every horizon, yet the earlier rules still said VALIDATED. Stored models are re-judged under the new rules (`VERDICT_VERSION`) and retrain on the next run.
- Apply point-in-time price, liquidity and history filters inside the ranker (as the probability models already did), so rows are judged by what they were on each session, not by today's values.
- Report a plain momentum top 10 beside the ranker's top 10 in the terminal, Markdown and HTML reports, and show edge over random and over momentum in every evidence line.
- Add progress lines (`[ranker]`, `[evidence]`) and a training time budget (`ranker_time_budget_seconds`, default 900); skipped horizons are retried next run.
- Bound the filings evidence query to the last three years so it uses the time index instead of scanning the whole table.
- Add `scripts/paper_ledger.py` to log each run's picks and score matured ones against SPY and against each other; see docs/PAPER_LEDGER.md.
- Add a memory regression test for the filtered loaders.

## 2.4.1

- Fix out-of-memory kills on 12 GB machines. A live run was killed with about 10.6 GB resident during feature construction. Causes: every stored SEC fact (all concepts, namespaces and units) and every filing was decoded into Python dictionaries, and label construction held one dictionary per row, per horizon, in memory.
- SEC ingestion now stores only the us-gaap USD/shares concepts the features and reports actually read (`fundamentals.FACT_CONCEPTS`). Already-stored facts are not deleted; they are simply never loaded.
- `Store.records()` gains SQLite-side filters (`concepts`, `forms`, `symbol`) and a `fields` projection; new `Store.recent_by_symbol()` loads only the newest filings per issuer for report evidence. Results for the data the application uses are unchanged.
- `make_labels` builds and concatenates per-symbol DataFrames instead of one list of row dictionaries.
- Holdings event alerts now query each holding's 8-K filings directly, so Form 4 volume can no longer push an adverse 8-K out of the last-six-filings window.
- Add docs/LOW_MEMORY.md with laptop settings and diagnostics.

## 2.4.0

- Add the alpha ranker: cross-sectional model of excess return over SPY, trained on per-session percentile ranks, judged on a purged holdout of non-overlapping cohorts against momentum and random baselines, and labelled NOT VALIDATED unless pre-declared checks pass. See docs/RANKER.md.
- Add a risk-capped top 10 (common stocks, adequate data, price floor, 30D P(loss >10%) cap) to the terminal, Markdown and HTML reports, with holdings shown directly beneath it.
- Add holdings alerts: ATR trailing stop, negative model outlook and adverse 8-K items, plus optional desktop notification.
- Fix SEC ingestion: a missing company-facts document (HTTP 404) is now an issuer property, not a provider outage. It previously wedged the SEC refresh on one issuer and blanked the fundamentals family.
- Raise the default SEC batch to 200 issuers per run; example configs use a 50 dollar price floor.

## 2.3.0

## 2.3.1

- Respect acquirer/target roles in merger return processing; parse stock-dividend multipliers separately from split ratios. Preserve genuinely unresolved events and typed identifiers.
- Rank adequate-data research ahead of limited-data research without changing forecast estimates or hiding candidates.
- Deduplicate report evidence through a versioned symbol-reference export; preserve historical forecasts and schema 5.
- Add regression tests for action roles, dividend entitlements, data-quality ordering and lossless report compaction.


- Replace unusable historical promotion windows with frozen paired forward trials, fixed evaluation policy, immutable entry/outcome records and explicit progress.
- Add bounded quarterly earnings history, causal sector ETF affinity, accession-matched cash flow/margins/share trends, and licensed analyst/options export imports.
- Add current data-quality/coverage diagnostics and per-horizon prospective Top 3/20 outcomes after costs.
- Preserve strict qualification, legacy models, user settings and all historical records through additive schema 5 migration.

## 2.2.0

- Rank research by probability and downside risk before expected return; retain strict qualification and consistency categories.
- Preserve leaderboard layout while replacing raw HTML JSON/debug dumps with compact status, validation and performance tables.
- Add stored prior-run Top-20 comparisons without invented attribution or randomized changes.
- Add causal advanced-price, market, filing and deterministic news challenger features; keep unavailable external signals optional.
- Add ExtraTrees and a training-only empirical coherent-distribution challenger with development selection gates.
- Separate promotion comparison from the final holdout and exclude the incumbent's already inspected history.
- Preserve schema 4, existing models, healthy runtime data and user configuration.

## 2.1.0

- Separate research rankings, strict qualification and prospective cohorts.
- Persist all live research snapshots with atomic durable deduplication.
- Add conservative cross-model consistency flags, full validation diagnostics and all-format research reports.
- Add batched recent-market screening, bounded selected-universe history expansion, user-managed watchlist and open-forecast price refresh.
- Add schema 4 non-destructive metadata tables, bounded enrichment and generated-report retention.
- Preserve namespace protections, corporate-action transaction batching and temporal model validation.

# Changelog

## 2.0.5

- Add a bounded diversified starter profile and independent news lookback.
- Respect explicit news/SEC/FRED/Alpha switches in ingestion, live doctor, features and evidence.
- Filter restricted corporate actions using authoritative tickers with universe-scoped durable cursors.
- Preserve price-only training, disclose selected inputs, and backfill earlier price prefixes in durable windows.
- Add offline provider, resume, history-expansion and four-horizon starter integration regressions.

## 2.0.4 — 2026-10-07

- Keep pip downloads, build/rollback temporary files and installer caches under ignored `.stox-local/`; install dependencies into the repository `.venv`. Disable inherited pip user/target/prefix settings that could redirect installation.
- Reuse an existing Python 3.11–3.13 venv instead of recreating it with a newer system interpreter. Check installed/source versions and dependency consistency before declaring setup complete.
- Resolve the default config beside the source checkout from any launch directory. Preserve explicit config/root overrides and fix their precedence when the TOML also specifies a root.
- Route Python, SQLite and joblib temporary files and XDG caches under the selected runtime root; restore environment settings for embedded callers. Add read-only `stox paths` and setup write/fsync/rename/read preflight checks.
- Preserve the schema-3 bootstrap repair and existing runtime state. No change can repair a device reporting unrecovered medium read errors.

## 2.0.3 — 2026-10-07

- Batch event/identifier writes in caller-owned FULL-durability transactions; add atomic page/cursor receipts and bounded date-window resume for actions/news.
- Preserve existing runtime data with schema-3 migration and resumable raw-action receipt backfill; avoid repeat normalization and payload insertion on legacy replay.
- Replace expensive status scans with read-only indexed metadata, writer liveness, stage progress and cached summaries. Add opt-in deep diagnostics/integrity checks and explicit compaction.
- Stop timestamp-only ticker-identifier growth and event-ID contamination of asset validation; recognize listed trust-series fund structures without a ticker allowlist.
- Reuse bar summaries, partial durable bar history, completed SEC responses, FRED offsets, feature/label caches and trained horizons; add stage summaries and retry jitter.
- Add fault-injection, full-engine bootstrap/resume, migration, transaction-count and large-status regression tests. No runtime databases, credentials or reports are shipped.

## 2.0.2 — 2026-10-03

- Added typed security identity and authoritative active/tradable asset validation at universe and bars boundaries; fixed inactive/non-ticker assets and unvalidated holdings/config inputs reaching price ingestion.
- Preserve CUSIP/ISIN/FIGI/asset/CIK/prior-ticker and corporate-action provenance without promoting event identifiers into the universe.
- Isolate provider-rejected tickers with persistent quarantine; retain systemic error failures and enforce an aggregate identity-failure budget.
- Added universe counts/reasons, shared downstream eligibility and additive schema-2 migration.
- Verify durable completed-session data before trusting checkpoints; audit-invalidate false progress while preserving partial downloads and idempotent resume.
- Added exact `09175M861` fixtures and regression coverage for identity separation, private securities, rename/reverse split provenance, quarantine, partial failures, migration and duplicate-free resume.

## 2.0.1 — 2026-10-03

- Fixed the SIP doctor query starting after daily bars' midnight timestamps; use five completed historical calendar sessions and a strict 16-minute safety margin, including immediately after close.
- Validate actual finite completed SIP OHLCV bars and explain empty/invalid HTTP 200 responses with sanitized request-window/count diagnostics.
- Replaced the combined FRED/ALFRED canary with a small DGS10 observation check; added optional independent `doctor --live --vintages` as-of validation.
- Preserve useful HTTP error messages while redacting request credentials, credential fields and encoded echoes; bound diagnostic output.
- Added offline regression coverage for temporal boundaries, valid/invalid observations, vintage isolation, provider restriction errors and credential redaction.

## 2.0.0 — 2026-10-03

- Clean-sheet local, on-demand application with 20/30/60/90-calendar-day forecasts.
- Added Alpaca/SEC/FRED and budgeted optional Alpha Vantage adapters, checkpointed ingestion and revision provenance.
- Added purged time-series modeling, separate calibration, holdout evaluation, qualification and champion/challenger tracking.
- Added immutable forecast history, next-open paper entries, per-horizon total-return outcomes and manual holdings reviews.
- Added terminal/HTML/JSON/Markdown/AI-review reports, operator commands, secret-safe setup, storage gates and offline tests.
- Documented unavailable live validation and remaining corporate-action, historical-universe, event and scale limitations.

