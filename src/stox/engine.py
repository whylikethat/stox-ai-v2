from __future__ import annotations
import json
import logging
import subprocess
import uuid
from logging.handlers import RotatingFileHandler
import pandas as pd
from threadpoolctl import threadpool_limits
from . import __version__
from .calendar import MarketCalendar,utc
from .storage import Store,encode,now_iso,digest
from .security import SecurityMaster
from .ingestion import Ingestor
from .features import build_features,make_labels,FEATURE_VERSION
from .models import registry_train,needs_training_labels
from .ranking import forecast_rows,leaderboards,rank
from .predictions import settle,issue,performance
from .holdings import review
from .reporting import evidence,write_reports,index_evidence
from .progress import Progress
from .paths import runtime_environment


def git_commit():
    try:
        from pathlib import Path
        return subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).resolve().parents[2],stderr=subprocess.DEVNULL,text=True).strip()
    except (OSError,subprocess.CalledProcessError):
        return 'unknown (installed distribution)'

def run(cfg,offline=False,force_train=False,timestamp=None,synthetic=False):
    with runtime_environment(cfg.root):
        return _run(cfg,offline,force_train,timestamp,synthetic)


def _run(cfg,offline=False,force_train=False,timestamp=None,synthetic=False):
    cfg.initialize()
    store=Store(cfg.root)
    cal=MarketCalendar()
    started=utc(timestamp).isoformat()
    rid=started.replace(':','').replace('+','_')+'-'+uuid.uuid4().hex[:8]
    handler=RotatingFileHandler(cfg.root/'logs'/'stox.log',maxBytes=2_000_000,backupCount=3)
    logger=logging.getLogger('stox')
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    progress=Progress(store,rid)
    try:
        with store.lock():
            store.storage_check(cfg.soft_storage_gb,cfg.hard_storage_gb)
            with store.db:
                store.db.execute("UPDATE runs SET status='interrupted',finished_at=? WHERE status='running'",(now_iso(),))
                store.db.execute("UPDATE stages SET status='interrupted',finished_at=? WHERE status='running'",(now_iso(),))
                store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(rid,started,None,'running','{}'))
            warnings=[]
            if not offline:
                session,warnings=Ingestor(cfg,store,cal,progress=progress).run(started)
            else:
                session=cal.completed(started)
                warnings.append('Offline run: no providers contacted; cached data only')
            if synthetic:
                warnings.append('SYNTHETIC DATA: model results are a software demonstration, not investment evidence')
            master=SecurityMaster(store)
            master.ensure(started)
            master.resolve_holdings()
            master.check_failure_budget(cfg)
            assets=master.assets()
            symbols=[r.ticker for r in master.requests(cfg)]
            for benchmark in ('SPY','QQQ'):
                if benchmark not in symbols:
                    raise RuntimeError(f'FORECASTING BLOCKED: {benchmark} is not an authoritative queryable asset')
            for benchmark in ('SPY','QQQ'):
                b=store.bars(benchmark)
                if b.empty or b.session.max()!=session:
                    raise RuntimeError(f'FORECASTING BLOCKED: {benchmark} lacks latest completed session {session}')
            expected_symbols={a['symbol'] for a in assets if a.get('status')=='active' and a.get('security_type') in ('common','etf') and a['symbol'] in symbols}
            summary={r[0]:r[2] for r in store.warehouse_summary()}
            covered=sum(summary.get(s)==session for s in expected_symbols)
            if expected_symbols and covered/len(expected_symbols)<cfg.min_core_coverage:
                raise RuntimeError(f'FORECASTING BLOCKED: latest-session coverage {covered}/{len(expected_symbols)} is below the configured minimum')
            from .ingestion import action_scope
            actions_checkpoint=store.checkpoint(action_scope(cfg,symbols)) or store.checkpoint('actions')
            if not actions_checkpoint:
                raise RuntimeError('FORECASTING BLOCKED: corporate-action feed has no successful checkpoint')
            if pd.Timestamp(actions_checkpoint) < cal.close(session):
                raise RuntimeError('FORECASTING BLOCKED: corporate-action checkpoint is stale')
            with progress.stage('features',total=len(symbols)):
                features,errors=build_features(store,symbols,cal,cal.close(session).isoformat(),cfg.max_training_rows,progress=progress,cfg=cfg)
                warnings.extend(f'{s}: {reason}' for s,reason in errors.items())
            with progress.stage('labels-and-settlement'):
                if needs_training_labels(cfg,store,features,started,force_train):
                    labels=make_labels(features,store,cal,cfg.horizons,cfg.friction_bps,progress=progress)
                    if labels.empty:
                        raise RuntimeError('FORECASTING BLOCKED: insufficient mature labels; bootstrap more history')
                    labels=labels[pd.to_datetime(labels.label_available_at,utc=True)<=utc(started)]
                else:
                    labels=pd.DataFrame()
                    progress.update(skipped=len(symbols),note='Compatible champions within evaluation cadence; label rebuild deferred',force=True)
                settlement=settle(store,cal,started,cfg.friction_bps)
                warnings.extend(settlement['warnings'])
            with progress.stage('models',total=len(cfg.horizons)):
                logger.info(encode({'run':rid,'stage':'models','label_rows':len(labels)}))
                from .features import FAMILIES
                health={r['provider']:r['status'] for r in store.db.execute('SELECT * FROM health')}
                if not synthetic:
                    for family,provider in (('news','news'),('fundamentals','sec'),('macro','fred')):
                        if health.get(provider) not in ('PASS',):
                            labels[FAMILIES[family]]=float('nan')
                from .trials import settle_trials,assess
                if not offline and not synthetic:
                    settle_trials(store,cal,started)
                    assess(store,cfg,started)
                models=registry_train(labels,cfg,store,force_train,started,progress=progress)
            if set(models)!=set(cfg.horizons):
                raise RuntimeError('FORECASTING BLOCKED: all active horizons need trained models; inspect stox status')
            with progress.stage('ranking-and-reports'):
                # Fresh event cutoff is the run snapshot. Preserve price cutoff independently.
                from .features import enrich
                current=features.sort_values('session').groupby('symbol').tail(1).copy()
                current['issue_at']=started
                current=enrich(current,store,cfg)
                rows=forecast_rows(current,assets,models,cfg,session)
                horizon_ranks={(r['symbol'],h):r['rank'] for h in cfg.horizons for r in rank([r for r in rows if r['horizon']==h])}
                for row in rows:row['horizon_rank']=horizon_ranks[(row['symbol'],row['horizon'])]
                health={r['provider']:r['status'] for r in store.db.execute('SELECT * FROM health')}
                from .features import FAMILIES
                for row in rows:
                    selected=set(models[row['horizon']].columns)
                    for family,provider,columns in (('news','news',FAMILIES['news']),('SEC','sec',FAMILIES['filings']+FAMILIES['fundamentals']),('macro','fred',FAMILIES['macro']),('earnings','alpha',FAMILIES['earnings']),('earnings history','earnings-history',FAMILIES['earnings']),('analyst','analyst',FAMILIES['analyst']),('options','options',FAMILIES['options'])):
                        if selected.intersection(columns) and health.get(provider)!='PASS' and not synthetic:
                            row['qualified']=False
                            row['exclusions'].append('Selected '+family+' source is degraded')
                    if row['exclusions'] and row['quality_label']=='High Confidence / Lower Return':
                        row['quality_label']='Research only / below preferred return'
                primary,across=leaderboards(rows)
                for row in rows:
                    row['primary_rank']=next((p['rank'] for p in primary if p['symbol']==row['symbol'] and row['horizon']==30),None)
                    row['across_rank']=next((p['rank'] for p in across if p['symbol']==row['symbol'] and p['horizon']==row['horizon']),None)
                from .ranker import run_ranker
                ranker,ranker_percentiles=run_ranker(cfg,store,cal,features,labels,current,rows,assets,started,force_train)
                if ranker['status']=='ERROR':
                    warnings.append('Alpha ranker failed and was skipped: '+ranker['error'])
                elif ranker['status'].startswith('RESEARCH'):
                    warnings.append('Alpha ranker has not passed its holdout validation checks; its top 10 is research, not evidence of an edge')
                from .fundamentals import FACT_CONCEPTS
                # Evidence needs only the last few filings per issuer and a handful of fact concepts; load just those.
                snapshots={kind:(store.recent_by_symbol(kind,started,6) if kind=='filings' else
                                 store.records(kind,started,concepts=FACT_CONCEPTS) if kind=='facts' else store.records(kind,started))
                           for kind in ('news','filings','facts','earnings','actions','disclosure_documents','disclosure_transactions')}
                snapshots=index_evidence(snapshots)
                context={s:evidence(store,s,started,cfg,snapshots) for s in {r['symbol'] for r in rows}}
                holdings=review(store,rows,rid)
                highlights={r['symbol'] for r in primary[:3]+across[:3]}|{h['symbol'] for h in holdings if h['status']!='HEALTHY'}
                for holding in holdings:
                    holding['evidence']=context.get(holding['symbol'],{})
                from .alerts import add_alerts,notify
                add_alerts(holdings,cfg,store,ranker_percentiles,started)
                if not synthetic and not offline and cfg.desktop_notifications:
                    notify(holdings)
                details=[]
                with threadpool_limits(limits=cfg.threads):
                    for symbol in sorted(highlights|{r['symbol'] for r in primary+across}):
                        forecasts=[r for r in rows if r['symbol']==symbol]
                        fr=current[current.symbol==symbol]
                        if not fr.empty and symbol in highlights:
                            for r in forecasts:
                                r['explanations']=models[r['horizon']].explain(fr.iloc[0].to_dict())
                        details.append({'symbol':symbol,'forecasts':forecasts,'evidence':context.get(symbol,{})})
                for row in rows:
                    row['catalyst_context']=context[row['symbol']]
                primary,across=leaderboards(rows)
                if any(not m.metadata['validated'] for m in models.values()):
                    warnings.append('One or more research models have not passed qualification; unvalidated horizons cannot issue official recommendations')
                warnings.append('Historical universe reconstruction is approximate; historical asset metadata and revisions are incomplete before first local observation')
                screen=store.db.execute("SELECT value FROM runtime_meta WHERE key='modeling_universe'").fetchone()
                if screen:
                    warnings.append(json.loads(screen[0])['note'])
                cleanup_models(store)
                metadata={'enrichment_profile':{k:getattr(cfg,k) for k in ('enable_news','news_lookback_days','enable_sec','enable_fred','enable_alpha_vantage')},'version':__version__,'git_commit':git_commit(),'run_started':started,
                          'market_session':session,'market_cutoff':cal.close(session).isoformat(),'event_cutoff':started,
                          'news_checkpoint':store.checkpoint('news'),'sec_checkpoint':store.checkpoint('sec'),'macro_checkpoint':store.checkpoint('fred'),
                          'model_ids':{str(h):m.model_id for h,m in models.items()},'model_feature_columns':{str(h):m.columns for h,m in models.items()},'feature_version':FEATURE_VERSION,
                          'data_digest':digest([(s,store.checkpoint('bars:'+s)) for s in symbols]),'universe_digest':digest(assets),'universe_diagnostics':master.persist_diagnostics(cfg,session),
                          'friction_bps':cfg.friction_bps,'provider_health':[dict(r) for r in store.db.execute('SELECT * FROM health')],
                          'run_mode':'synthetic' if synthetic else 'offline' if offline else 'live',
                          'modeling_universe':json.loads(screen[0]) if screen else {'mode':'explicit or cached'},
                          'ranking_rule':'Qualified first; data-limited research after adequate-data research; consistency, P(gain), downside risk, agreement, reliability, then returns; deterministic',
                          'limits':'No brokerage, trades, capital allocation, continuous monitoring, or tax advice'}
                # Issuance occurs after all computation, never backdated to data fetch start.
                issued=started if synthetic else now_iso()
                metadata['forecast_issued_at']=issued
                before=store.db.execute('SELECT count(*) FROM forecasts').fetchone()[0]
                if not synthetic and offline:
                    warnings.append('Offline research runs do not issue live prospective forecasts')
                    ids=[]
                else:
                    ids=issue(store,cal,rows,issued,metadata)
                    if not synthetic:
                        from .trials import issue_trials
                        issue_trials(store,current[current.session==session],models,cfg,cal,issued)
                created=store.db.execute('SELECT count(*) FROM forecasts').fetchone()[0]-before
                if ids and not created:
                    warnings.append('Existing research snapshot reused; no duplicate forecasts created')
                conflicts=sum(r['consistency_status']=='conflicting' for r in rows)
                if conflicts:
                    warnings.append(f'{conflicts} forecasts have conflicting probability/return estimates; ranked conservatively and never qualified')
                from .models import validation_diagnostics
                qualified_ids=[fid for fid,symbol in zip(ids,sorted({r['symbol'] for r in rows})) if any(r['qualified'] for r in rows if r['symbol']==symbol)]
                from .trials import summary as trial_summary
                from .audit import data_audit
                report={'model_progress':trial_summary(store),'data_audit':data_audit(features,rows,cfg),'run_id':rid,'synthetic':synthetic,'metadata':metadata,'warnings':warnings,'primary_30d':primary,
                        'across_horizons':across,'details':details,'research_candidates':rows,'holdings':holdings,'ranker':ranker,
                        'prospective':performance(store),'backtests':{str(h):m.metadata['evaluation'] for h,m in models.items()},
                        'research_forecast_ids':ids,'new_forecast_count':created,
                        'official_forecast_ids':qualified_ids,'qualified_forecast_ids':qualified_ids,'settlement':settlement,
                        'qualified_30d':rank([r for r in rows if r['qualified'] and r['horizon']==30])[:20],
                        'qualified_across_horizons':rank([r for r in rows if r['qualified']])[:20],
                        'by_horizon':{str(h):rank([r for r in rows if r['horizon']==h]) for h in cfg.horizons},
                        'validation':{str(h):validation_diagnostics(m.metadata,cfg) for h,m in models.items()}}
                store.meta('validation',report['validation'])
            with progress.stage('report-export'):
                path=write_reports(cfg.root,report)
                from .reporting import cleanup_reports
                cleanup_reports(cfg.root,cfg.report_retention_runs)
            with store.db:
                store.db.execute('UPDATE runs SET finished_at=?,status=?,metadata=? WHERE id=?',(now_iso(),'success',encode(metadata),rid))
            logger.info(encode({'run':rid,'stage':'complete','research_snapshots':len(ids)}))
            print('Stage summary: '+', '.join(r['name']+'='+r['status'] for r in store.db.execute('SELECT name,status FROM stages WHERE run_id=? ORDER BY started_at',(rid,))),flush=True)
            return report,path
    except BaseException as exc:
        with store.db:
            store.db.execute('UPDATE runs SET finished_at=?,status=?,metadata=? WHERE id=?',(now_iso(),'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',encode({'error':str(exc) or type(exc).__name__}),rid))
        logger.error(encode({'run':rid,'stage':'failed','error':str(exc)}))
        raise
    finally:
        logger.removeHandler(handler)
        handler.close()
        store.close()


def cleanup_models(store):
    """Bound unreferenced artifacts; keep registry/audit metadata and every issued model."""
    referenced={r[0] for r in store.db.execute("SELECT candidate FROM model_trials WHERE status='collecting' UNION SELECT incumbent FROM model_trials WHERE status='collecting'")}
    for r in store.db.execute('SELECT payload FROM forecasts'):
        referenced.update(p['model_id'] for p in json.loads(r[0])['forecasts'] if p.get('model_id'))
    for h in [r[0] for r in store.db.execute('SELECT DISTINCT horizon FROM models')]:
        rows=store.db.execute('SELECT * FROM models WHERE horizon=? ORDER BY created_at DESC',(h,)).fetchall()
        keep={r['id'] for r in rows[:3]}|{r['id'] for r in rows if r['status']=='champion'}|referenced
        for r in rows:
            if r['id'] not in keep:
                (store.root/r['path']).unlink(missing_ok=True)
    for directory in ('cache','warehouse','models'):
        for p in (store.root/directory).rglob('*.tmp'):
            p.unlink(missing_ok=True)

