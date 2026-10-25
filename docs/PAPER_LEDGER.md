# Paper ledger

The only evidence that settles whether any list works is what happens after you record it. This script keeps that record.

```bash
stox run
python scripts/paper_ledger.py log      # appends the ranker top 10 and the momentum top 10 for today
python scripts/paper_ledger.py score    # scores entries at least 30, 60 and 90 days old against SPY and each other
```

- Data root defaults to `.stox`; override with `--root`. The ledger is `<root>/paper_ledger.csv`.
- Logging twice on the same day adds nothing. Run it after each `stox run`, about monthly.
- Entry price is the report's close, so results ignore spreads, next-open fills and taxes.
- The unit of evidence is the cohort (one logged day). Trust a list only after several matured cohorts that beat SPY
  and the momentum list. Until then, it is a hypothesis, not a track record.
