# Running on a 12 GB laptop (2.4.1)

## What was wrong

A live `stox run` was killed by the Linux out-of-memory killer (about 10.6 GB resident of 11 GB) during feature
construction. The cause was loading data the model never reads: every SEC fact (tens of thousands per issuer) and
every filing became Python dictionaries, and label construction kept one dictionary per row and horizon.
2.4.1 loads only the facts, filings and fields the features and reports use, and builds labels per symbol.

## Diagnose

```bash
sudo dmesg -T | grep -iE "out of memory|killed process" | tail -3   # anon-rss is the process size when killed
free -h && swapon --show
watch -n2 free -h                                                   # run in a second terminal during stox run
```

## Safety net: swap on the internal disk (never on NTFS or the external drive)

```bash
sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
```

## Optional lighter settings in config.toml

These reduce time and memory if training is still slow. They trade some model breadth for speed.

```toml
max_training_rows = 120000
horizons = [30, 60, 90]
ranker_horizons = [30, 60, 90]
sec_symbols_per_run = 100
```

Stored SEC data is not deleted by this release, so the database file may stay large; only what is loaded changed.
