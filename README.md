# STOX-AI 2.3 forward evaluation

Frozen challenger trials, bounded earnings history, sector ETF proxies and compact model/data diagnostics. See [final product operations](docs/FINAL_PRODUCT.md). Existing data is upgraded non-destructively.

# STOX-AI 2.2 — probability-first weekly research

See [Weekly upgrade](docs/WEEKLY_UPGRADE.md) for the preserved-table HTML cleanup, prior-run comparisons, feature challengers and validation limitations. Existing models and runtime data are retained.

# STOX-AI 2.1 — research forecasts with separate strict validation

For the complete healthy-drive migration, exact production profile and Kali commands, see [Production upgrade](docs/PRODUCTION_UPGRADE.md). Research rankings are always visible when core data/models are available; qualification never filters them out.

# STOX-AI

**2.0.5 fast-start:** use [the bounded starter profile](docs/FAST_START.md) for 110 diversified stocks/ETFs, price history from 2023, and optional enrichment explicitly disabled. News now has its own lookback. The guide gives fresh-SSD setup, checks, bootstrap/run and later expansion commands.

**2.0.4 local storage:** setup now keeps its downloads/build files inside the repository, reuses a supported existing Python environment, and verifies installation. Runtime temporary files stay under the selected data root. `stox paths` shows all locations without opening the database. See [local installation and recovery](docs/LOCAL_STORAGE.md).

**2.0.3 bootstrap repair:** if you have an existing interrupted bootstrap, follow the [non-destructive upgrade and resume procedure](docs/BOOTSTRAP_REPAIR.md) before restarting. It preserves `.stox`, adds page checkpoints and batches database writes. `stox status` is now a fast read-only command.

Local, on-demand U.S. stock and ordinary-ETF research for **20, 30, 60 and 90 calendar days**, with 30D as the primary horizon. The routine command is `stox run`. No trades, brokerage account access, capital allocation, paid AI API, email, daemon or cloud runtime.

**Release status:** offline-tested software with real provider adapters. No live credentials or real-market training were used in development. A successful synthetic demonstration proves software behavior, not forecasting accuracy. Live data access, full-universe bootstrap duration and prospective usefulness must be established locally. The engine can legitimately produce no qualifying opportunities, including for an extended period. Read [known limitations](#limitations) before interpreting results.

## Install on Kali Linux

Use a local Linux filesystem (ext4 recommended) on the mounted external drive. Do not place an active SQLite database on a network filesystem. Python **3.12** is the tested runtime; the package supports Python 3.11–3.13 subject to availability of the pinned dependency wheels.

```bash
sudo apt update
sudo apt install python3 python3-venv git
# Clone once, or pull the existing checkout.
git clone https://github.com/whylikethat/stox-ai.git
cd stox-ai
bash scripts/setup.sh
source .venv/bin/activate
```

Use apt for the system interpreter and a virtual environment for the exact scientific dependencies. Do not install pip packages into Kali's system Python. `requirements.lock` pins the tested dependency set; `pyproject.toml` documents supported dependency ranges. No particular drive mount path is assumed. If the drive changes paths and the virtual environment no longer starts, recreate `.venv` with `scripts/setup.sh`; the data root is independent of it.

Setup creates `config.toml` and `.secrets/providers.env` only if absent. Edit the latter **locally**:

```dotenv
ALPACA_API_KEY=
ALPACA_API_SECRET=
FRED_API_KEY=
ALPHAVANTAGE_API_KEY=
SEC_USER_AGENT=
```

Alpaca key and secret are core. Use an Alpaca paper API key for the read-only asset metadata endpoint. `SEC_USER_AGENT` must identify STOX-AI and a contact email, for example `STOX-AI contact name@example.com`. FRED is supporting enrichment; Alpha Vantage is optional. No SMTP or AI key is used. Environment variables override the secrets file. Secret values are excluded from config displays and provider errors.

## First use

```bash
stox doctor                 # Local checks, zero network calls
stox doctor --live          # Explicit, low-volume provider checks
stox doctor --live --vintages # Optional separate ALFRED snapshot check
stox bootstrap             # Resumable historical ingestion, training and reports
```

The live doctor checks Alpaca assets, historical SIP bars, news and corporate actions; SEC; a simple FRED DGS10 observation; and Alpha Vantage only if configured. `--vintages` adds a separately reported ALFRED as-of snapshot check. An ALFRED warning does not change the ordinary FRED result. PASS means the endpoint responded as expected, not that its entire historical dataset is complete. Core failures return a nonzero exit status. Optional failures produce WARN. The SIP doctor uses five completed historical sessions, with New York midnight boundaries and an end timestamp older than the Basic recent-data window. It works during market hours, immediately after close, on weekends and on holidays. This connectivity canary does not require today's bar or bypass the stricter freshness gate used by `stox run`. Provider HTTP errors include bounded, credential-redacted response details.

The first full-universe bootstrap may take many hours, especially SEC history and provider-limited ingestion. Progress is durable per security and event page; explicit stage summaries show progress. Legacy unfinished event feeds need the receipt backfill described in the upgrade guide. Ctrl-C then rerun the same command to resume. No training model or market data is shipped in Git. Keep the external drive mounted while running.

## Normal operation

```bash
cd /path/to/stox-ai
source .venv/bin/activate
stox run
stox report                # Prints the local HTML report path
stox status
```

`stox run` catches up from durable checkpoints after a day, week or longer absence; refreshes sources; rebuilds changed price-feature caches; settles available forecast outcomes; trains challengers when due; ranks the active horizons; reviews holdings; and exports reports. Current prices must include the latest completed XNYS regular session. The command deliberately blocks during the first 16 minutes after a close rather than querying restricted recent SIP data or silently using an older session.

Repeated runs with the same market/model forecast snapshot do not issue duplicate official forecasts. An offline research run is available as `stox run --offline`; it does not issue new official forecasts. `stox run --retrain` forces challenger evaluation, not promotion. `stox bootstrap` uses the same resumable pipeline and does not erase prior state.

Configuration is read from `config.toml` in the working directory, or `--config /path/to/config.toml`, or `STOX_CONFIG`. Relative storage roots resolve beside the config file. `STOX_HOME` or the global `--root` option overrides storage. Example:

```bash
stox --config /mnt/drive/stox-ai/config.toml run
```

## Reports and interpretation

The default storage root is `.stox/`, with:

```text
.stox/reports/latest.html
.stox/reports/latest.json
.stox/reports/latest.md
.stox/reports/latest_ai_review.md
.stox/reports/runs/<run-id>/
```

Open `latest.html` in a browser. Each successful run has immutable timestamped copies; latest aliases are replaced atomically. Report versions contain UTC timestamps with explicit offsets, independent price/event cutoffs, source health, model IDs and data fingerprints. Upload `latest_ai_review.md` to an independent reviewer without adding instructions: it embeds review instructions, the Top-20 context and focused candidate/holdings evidence. Provider text is explicitly labeled as untrusted evidence.

**Primary 30D:** qualified one-month opportunities. **Best Across All Horizons:** qualified security/horizon pairs, permitting longer horizons to win. Stocks and ETFs have equal ranking treatment. Calibrated P(gain) leads; expected return resolves differences smaller than the comparable calibration uncertainty. This resolution may be wide with weak support; sample counts and intervals expose that uncertainty. Return targets (1.5%, 2%, 4%, 6%) are soft labels, never mandatory cutoffs. No list is filled to reach 20 names.

All four horizon outputs accompany each ranked security. Downside exceedance probabilities, quantiles, empirical interval coverage, alternative-model disagreement, liquidity and source/model limitations are included. A statistical interval is not a guarantee. Unvalidated outputs remain in the JSON research section and cannot become recommendations. A forecast may have a high estimated win probability and still fail sample, calibration, liquidity or tail criteria.

The official paper entry is the next regular session open strictly after actual issuance. Entry is pending until the daily raw SIP open is available at a subsequent run. The horizon starts on that entry date; settlement is the first regular session close on/after entry + N calendar days. Daily SIP open/close are reproducible paper-price references, not a guarantee of an auction fill. Dividends are accrued without reinvestment; splits update units; configurable round-trip friction defaults to 20 basis points. USD only, no FX modeling or position sizing.

## Alpha ranker and top 10 (2.4.0)

Reports now open with a **Top 10 — alpha ranker** table (expected excess return over SPY, risk-capped) followed by
**Your holdings**, with alerts first. The ranker is labelled NOT VALIDATED until its holdout checks pass, and the
evidence lines show exactly what it earned on data it never trained on. Read `docs/RANKER.md` before relying on it. Keep your own evidence with `scripts/paper_ledger.py` (`docs/PAPER_LEDGER.md`).

## Holdings

```bash
stox holdings add AAPL --date 2026-09-15 --price 230.50 --shares 20
stox holdings list
stox holdings remove 1
```

Date and price can be prompted interactively; shares are optional. Removal marks a holding inactive while preserving history. A matching prior forecast within seven days can be linked. Reviews show current horizons, original outlook when available, USD total-return P/L, known news/events and deterioration. `EXIT REVIEW` requests attention; it does not execute or guarantee a sell decision. Unknown earnings dates are stated as unknown. Holdings are evaluated only when the application runs.

## Evaluation and model updates

Logistic and histogram-boosting candidates are evaluated in chronological walk-forward folds, with purged overlapping labels and a five-calendar-day embargo. Separate later calibration data fit sigmoid calibrators; a final untouched holdout evaluates the selected recipe. Optional feature families require development-fold improvement over price/volume. Six calibrated return-threshold classifiers estimate gain and loss probabilities; regression and quantile models provide return/distribution estimates. Monotone projection prevents inconsistent threshold probabilities.

A champion registry retains the current model unless a challenger improves paired out-of-sample Brier loss with a positive block-level lower confidence bound and no calibration deterioration. Block-aware support reduces the false precision caused by overlapping labels and correlated securities. Promotion and qualification are separate: a trained research champion may not qualify for recommendations. Every issued forecast references its exact model and report provenance.

Backtest statistics and genuine prospective outcomes are stored separately. Only qualified forecast/horizon outcomes enter primary prospective statistics. Top-1/Top-3 backtest equity summaries use non-overlapping cohorts; they are hypothetical evaluation strategies, not capital-allocation advice. SPY/QQQ comparisons provide context without replacing the absolute-positive-return objective.

## Tests and offline demo

```bash
bash scripts/self-test.sh
stox --root /tmp/stox-synthetic-demo demo
```

The test suite blocks socket connections and uses deterministic fixtures. The synthetic demo seeds its own directory, trains all four horizons and writes all report formats. Never reuse a production data directory for demonstration data. Synthetic fixture issuance/settlement and deterioration are tested separately even if no model recommendation qualifies. No live-market validation is claimed from these tests.

## Storage, shutdown and upgrades

SQLite holds transactional audit state; compressed per-symbol Parquet stores market history and feature caches; DuckDB supplies warehouse summaries. The feature builder bounds development rows with stable symbol/date sampling on large universes. Default soft/hard budgets are 100/150 GB and writes stop with less than 500 MB free. These are safety gates, not a measured full-universe size guarantee. Logs rotate, interrupted temporary files are cleaned after successful runs, and unreferenced older model artifacts are bounded. Issued model artifacts, model metadata, forecasts and outcomes are retained.

Stop with Ctrl-C, wait for process exit, then unmount the drive. There is no background daemon. For a manual backup, stop the application and copy the entire data root and configuration to another drive; keep credentials private. Restore that directory and select its root in config. No recurring backup archives are created.

Before upgrading, stop STOX-AI. Preserve the entire existing data directory; a separate backup is optional. For 2.0.3 follow the upgrade guide above. For ordinary source updates, run `git pull --ff-only`, then `bash scripts/setup.sh`, `source .venv/bin/activate`, `stox doctor`, and the tests. The database rejects newer unsupported schema versions rather than guessing a downgrade. Schema version 2 adds security identity, quarantine and history-ownership tables plus holding-resolution fields. The migration preserves existing bars, event revisions, holdings, forecasts/outcomes and valid checkpoints; no data deletion or reset is required.

## Troubleshooting

- **401/403:** verify local Alpaca key/secret and historical-data entitlements with `stox doctor --live`; never paste secrets into an issue.
- **429:** requests back off with bounded retries. Rerun after the provider window resets; successful checkpoints are retained.
- **FORECASTING BLOCKED:** inspect `stox status` and `.stox/logs/stox.log`. Missing SPY/QQQ, inadequate universe coverage, stale actions, incomplete models or unsafe storage prevent new forecasts. The last successful reports remain intact.
- **DEGRADED sources:** optional failures are reported. A champion that requires an unavailable family cannot qualify using silent imputation of the missing source.
- **No opportunities:** inspect validation and calibration support. Do not lower thresholds simply to force recommendations.
- **Insufficient training dates:** extend `history_start` when the provider supplies appropriate history; short histories cannot validate 90D forecasts well.
- **Interrupted report writing:** numerical forecasts already committed remain immutable; rerun to regenerate reports without duplicate issuance.
- **Moved external drive:** select the correct config/root and recreate the virtual environment if needed.

## Limitations

Free sources cannot certify a complete point-in-time historical security master. Active and inactive Alpaca assets are retained; unknown instrument classifications are excluded conservatively. Delisted coverage, renamed histories and pre-observation revisions remain incomplete. Deterministic sampling changes historical cross-sectional rank context on large universes. Current asset metadata must not be mistaken for a perfect historical universe.

Corporate actions are revision-stored. Cash distributions and ordinary split ratios have numeric handling. Complex mergers, spin-offs, rights and unresolvable stock distributions are deliberately quarantined; affected series/outcomes do not receive guessed returns. Automatic successor-security valuation is not implemented. This reduces coverage and is an explicit remaining gap versus full corporate-action support.

Historical bars are the provider's available reconstructed history, not a certified archive of every original release. Local revisions preserve prior observations, and affected pre-correction feature windows are excluded from labels; older unseen corrections cannot be recovered. Daily volume is lagged one session to avoid using later extended-hours volume in an earlier after-close backtest. Daily SIP price references are not exchange auction certification.

SEC evidence includes filing metadata and company-fact observations, not complete semantic summaries of filing documents. FRED vintage dates receive a conservative next-day availability lag. News supplies provider headlines/summaries; no linked sites are scraped, and previously unseen older text revisions cannot be reconstructed. The bounded correction overlap does not guarantee capture of every late historical edit.

Earnings/event coverage is incomplete without optional enrichment. Full-universe provider throughput and 12-GB hardware memory use need local measurement; only bounded synthetic workflows were exercised here. Optional family selection and a calibration method do not by themselves establish useful real-world forecasts. Quantile coverage and extreme-tail estimates require continued scrutiny; meaningful prospective evidence takes months.

See [ARCHITECTURE.md](ARCHITECTURE.md), [DATA_PROVENANCE.md](DATA_PROVENANCE.md), [MODEL_CARD.md](MODEL_CARD.md) and [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md).


## Security identities and recovering a failed bootstrap (2.0.2)

Only an authoritative Alpaca asset with an active, tradable U.S. equity/ETF ticker, supported exchange and accepted common-stock/standard-ETF classification enters price ingestion. Tickers, Alpaca asset IDs, CUSIPs, ISINs, FIGIs, SEC CIKs, provider IDs and prior tickers are stored separately. Corporate actions, news, SEC records, configured symbols and manual holdings cannot promote an identifier into the trading universe. The bars adapter requires a typed, validated ticker reference. The fixed SPY doctor canary is a deliberate separate preflight path.

Corporate actions retain all original identifiers and their roles, including non-traded distribution securities. An explicit identifier lookup can resolve only an unambiguous authoritative asset mapping; events cannot create that mapping. Renames/reverse splits preserve identifier provenance without automatically stitching price histories or rewriting holdings. A ticker reused by a different asset is quarantined instead of merged with an already bound history.

A symbol-specific provider rejection quarantines that asset, logs its reason, and continues with other securities. It is retried after seven days or when authoritative metadata changes. Authentication, rate-limit, server and general request errors still fail the run. At least two failed intended active/tradable assets exceeding the configured 5% identity-failure budget stop processing with an aggregate diagnostic. These limits are configurable in `config.toml`.

Bootstrap and `stox universe` calculate discovery, classification, inactive, non-queryable, identifier-failure, missing-history, price/liquidity and final eligible counts. `stox universe` includes individual exclusion reasons. `stox status` reports the last saved summary and its timestamp. Categories overlap; classification counts alone do not imply recommendation eligibility. Prices/history must be downloaded before data-dependent gates can be evaluated; fresh observations continue to update below-threshold securities so eligibility can recover. Recommendations retain the configured $5 minimum, liquidity/history and model gates.

To resume an interrupted bootstrap, keep the same configuration and data root, follow the [2.0.3 upgrade guide](docs/BOOTSTRAP_REPAIR.md); for normal subsequent resumes use:

```bash
git pull --ff-only
bash scripts/setup.sh
source .venv/bin/activate
stox doctor --live
stox bootstrap
stox universe
stox status
```

Do not delete your database or warehouse. Completed symbols with verified durable bars are reused; partial symbols replay with idempotent merges. Legacy checkpoints pointing beyond durable history are invalidated with an audit record, and only those symbols are retried. Empty responses cannot mark a price stage complete. Existing inactive/non-queryable archives are retained but excluded from new recommendations. This does not certify historical listing membership or reconstruct missing rename/delisting chains.


Optional public SEC ownership and congressional transaction context: see [Public disclosures](docs/PUBLIC_DISCLOSURES.md) for coverage, setup, import commands and source restrictions.
