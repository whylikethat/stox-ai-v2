from __future__ import annotations
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from .paths import default_config

SECRET_NAMES = ('ALPACA_API_KEY', 'ALPACA_API_SECRET', 'FRED_API_KEY', 'ALPHAVANTAGE_API_KEY', 'SEC_USER_AGENT')

@dataclass
class Config:
    root: Path
    horizons: tuple[int, ...] = (20, 30, 60, 90)
    history_start: str = '2016-01-01'
    enable_news: bool = True
    news_lookback_days: int = 90
    enable_insider_disclosures: bool = False
    enable_house_disclosures: bool = False
    congressional_terms_acknowledged: bool = False
    disclosure_documents_per_run: int = 10
    disclosure_lookback_days: int = 90
    enable_analyst_signals: bool = False
    enable_options_signals: bool = False
    model_algorithms: tuple[str, ...] = ('logistic', 'histogram', 'extra_trees')
    enable_sec: bool = True
    enable_fred: bool = True
    enable_alpha_vantage: bool = True
    earnings_symbols_per_run: int = 3
    friction_bps: float = 20
    min_price: float = 5
    min_dollar_volume: float = 20_000_000
    min_history: int = 252
    min_core_coverage: float = .90
    max_identifier_failure_fraction: float = .05
    min_identifier_failures: int = 2
    max_participation: float = .005
    liquidity_reference_usd: float = 100_000
    soft_storage_gb: float = 100
    hard_storage_gb: float = 150
    av_daily_budget: int = 20
    event_refresh_minutes: int = 15
    retrain_days: int = 30
    min_new_labels: int = 500
    challenger_max_rows: int = 60000
    max_training_rows: int = 250_000
    min_validation_blocks: int = 8
    min_bucket_samples: int = 100
    max_calibration_error: float = .10
    max_tail_probability: float = .10
    threads: int = 2
    symbols: tuple[str, ...] = ()
    universe_size: int = 1000
    autonomous_universe: bool = True
    screen_lookback_days: int = 90
    screen_batch_size: int = 100
    screen_min_sessions: int = 20
    sec_symbols_per_run: int = 200
    ranker_enabled: bool = True
    ranker_horizons: tuple[int, ...] = (30, 60, 90)
    ranker_top_n: int = 10
    ranker_max_p_loss_10: float = .15
    ranker_min_cohorts: int = 6
    ranker_holdout_fraction: float = .25
    ranker_max_rows: int = 200_000
    stop_atr_multiple: float = 2.5
    desktop_notifications: bool = True
    report_retention_runs: int = 30
    news_pages_per_run: int = 100
    history_window_days: int = 365
    macro_series: tuple[str, ...] = ('DGS10', 'DGS2', 'UNRATE', 'CPIAUCSL', 'BAMLC0A0CM')
    secrets: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: str | None = None, root: str | None = None):
        config_path = Path(path or os.environ.get('STOX_CONFIG') or default_config()).expanduser().resolve()
        raw = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        base = config_path.parent
        values = raw.get('stox', {})
        configured_root = values.pop('root', '.stox')
        location = Path(root or os.environ.get('STOX_HOME') or configured_root).expanduser()
        if not location.is_absolute():
            location = base / location
        for key in ('horizons', 'symbols', 'macro_series', 'model_algorithms', 'ranker_horizons'):
            if key in values:
                values[key] = tuple(values[key])
        cfg = cls(root=location.resolve(), **values)
        for key in ('enable_news', 'enable_sec', 'enable_fred', 'enable_alpha_vantage','autonomous_universe','enable_insider_disclosures','enable_house_disclosures','congressional_terms_acknowledged','enable_analyst_signals','enable_options_signals'):
            if type(getattr(cfg,key)) is not bool:
                raise ValueError(f'{key} must be a boolean')
        if not 1 <= cfg.disclosure_documents_per_run <= 100 or not 1 <= cfg.disclosure_lookback_days <= 365:
            raise ValueError('Disclosure limits: 1–100 documents/run and 1–365 days lookback')
        if not 0 <= cfg.earnings_symbols_per_run <= 10 or not 1000 <= cfg.challenger_max_rows <= 250000:
            raise ValueError('Earnings limit must be 0–10 and challenger rows 1000–250000')
        if not cfg.model_algorithms or not set(cfg.model_algorithms)<= {'logistic','histogram','extra_trees'}:
            raise ValueError('Unsupported model_algorithms')
        if type(cfg.news_lookback_days) is not int or cfg.news_lookback_days < 1:
            raise ValueError('news_lookback_days must be a positive integer')
        if not 1 <= cfg.universe_size <= 1500 or not 1 <= cfg.screen_batch_size <= 100:
            raise ValueError('universe_size must be 1–1500; screen_batch_size must be 1–100')
        if not 1 <= cfg.history_window_days <= 365 or cfg.news_pages_per_run < 1 or cfg.screen_lookback_days < 30 or cfg.screen_min_sessions < 1 or cfg.sec_symbols_per_run < 0 or cfg.report_retention_runs < 1:
            raise ValueError('Invalid screening, enrichment or retention bounds')
        if 30 not in cfg.horizons or any(h <= 0 for h in cfg.horizons) or len(set(cfg.horizons)) != len(cfg.horizons):
            raise ValueError('Horizons must be unique positive calendar-day values including 30.')
        if not 0 <= cfg.event_refresh_minutes <= 1440:
            raise ValueError('event_refresh_minutes must be between 0 and 1440')
        if cfg.friction_bps < 0 or not 0 < cfg.soft_storage_gb < cfg.hard_storage_gb:
            raise ValueError('Invalid friction or storage limits.')
        if not 0 < cfg.max_identifier_failure_fraction <= 1 or cfg.min_identifier_failures < 2:
            raise ValueError('Invalid universe identifier-failure limits.')
        for key in ('ranker_enabled', 'desktop_notifications'):
            if type(getattr(cfg, key)) is not bool:
                raise ValueError(f'{key} must be a boolean')
        if not 1 <= cfg.ranker_top_n <= 50 or not 0 < cfg.ranker_max_p_loss_10 <= 1 or cfg.ranker_min_cohorts < 3:
            raise ValueError('ranker_top_n must be 1-50, ranker_max_p_loss_10 in (0,1], ranker_min_cohorts at least 3')
        if not .1 <= cfg.ranker_holdout_fraction <= .5 or cfg.ranker_max_rows < 5000 or cfg.stop_atr_multiple <= 0:
            raise ValueError('Invalid ranker holdout fraction, row cap or stop multiple')
        if any(h <= 0 for h in cfg.ranker_horizons) or not cfg.ranker_horizons:
            raise ValueError('ranker_horizons must be positive calendar-day values')
        secrets_path = base / '.secrets' / 'providers.env'
        if secrets_path.exists():
            for line in secrets_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    if key.strip() in SECRET_NAMES:
                        cfg.secrets[key.strip()] = value.strip().strip('\"\'')
        cfg.secrets.update({k: os.environ[k] for k in SECRET_NAMES if os.environ.get(k)})
        return cfg

    def initialize(self):
        for folder in ('warehouse', 'models', 'reports/runs', 'logs', 'cache'):
            (self.root / folder).mkdir(parents=True, exist_ok=True)

    def public(self):
        return {k: str(v) if isinstance(v, Path) else v for k, v in vars(self).items() if k != 'secrets'}

