from __future__ import annotations
import numpy as np
import pandas as pd
from collections import defaultdict
from pathlib import Path
from .actions import adjusted_history, relevant, total_return, UnresolvedAction, applies_to_returns
from .storage import digest
from .security import SecurityMaster

BASE_FEATURES = ['momentum_5','momentum_20','momentum_60','momentum_120','trend_50','trend_200','volatility','downside_vol','drawdown','volume_ratio','log_liquidity','gap','market_relative','beta','cross_rank']
ADVANCED_PRICE = ['momentum_10','momentum_40','momentum_acceleration','trend_20','trend_100','slope_50','slope_200','volatility_change','return_skew','tail_frequency','volume_acceleration','liquidity_stability','gap_mean']
SECTOR_ETFS = ('XLB','XLC','XLE','XLF','XLI','XLK','XLP','XLRE','XLU','XLV','XLY')
SECTOR_FEATURES = ['sector_proxy_relative','sector_proxy_momentum','sector_proxy_correlation']
MARKET_FEATURES = ['qqq_relative','iwm_relative','beta_adjusted_momentum','market_breadth_50','market_breadth_200','market_volatility','risk_on_off']
FAMILIES = {'price':BASE_FEATURES,'price_advanced':ADVANCED_PRICE,'market':MARKET_FEATURES,'sector_proxy':SECTOR_FEATURES,
 'news':['news_count','news_negative','news_count_1','news_count_3','news_count_7','news_abnormal','news_sentiment','news_regulatory','news_financing','news_earnings','news_ma'],
 'filings':['filing_count','filing_8k','filing_recency'],
 'macro':['macro_DGS10','macro_DGS2','macro_UNRATE','macro_CPIAUCSL','macro_BAMLC0A0CM','yield_curve'],
 'fundamentals':['assets_log','leverage','profit_margin','revenue_growth','operating_margin','cash_assets','free_cash_flow_assets','shares_growth'],
 'earnings':['earnings_days_until','earnings_surprise','earnings_days_since','earnings_positive_fraction'],
 'analyst':['analyst_eps_revision_30','analyst_sales_revision_30','analyst_revision_breadth'],
 'options':['options_iv30','options_iv60','options_iv90','options_skew','options_iv_rank']}
FEATURE_VERSION='causal-v5'

NEGATIVE = ('bankruptcy','fraud','recall','lawsuit','downgrade','investigation','default','misses')

def market_features(frame, actions, calendar):
    f = adjusted_history(frame,actions)
    w = f.wealth
    ret = w.pct_change(fill_method=None)
    for n in (5,10,20,40,60,120):
        f[f'momentum_{n}'] = w.pct_change(n,fill_method=None)
    for n in (20,50,100,200):
        f[f'trend_{n}'] = w/w.rolling(n).mean()-1
    f['volatility'] = ret.rolling(60).std()*np.sqrt(252)
    f['downside_vol'] = ret.clip(upper=0).rolling(60).std()*np.sqrt(252)
    f['drawdown'] = w/w.rolling(252,min_periods=60).max()-1
    f['volume_ratio'] = f.volume.shift(1)/f.volume.shift(1).rolling(20).median().replace(0,np.nan)
    f['dollar_volume'] = (f.close*f.volume).shift(1).rolling(60,min_periods=20).median()
    f['log_liquidity'] = np.log1p(f.dollar_volume)
    f['gap'] = [np.nan]+[total_return(a['close'],b['open'],a['session'],b['session'],actions,0) for a,b in zip(f.iloc[:-1].to_dict('records'),f.iloc[1:].to_dict('records'))]
    f['momentum_acceleration']=f.momentum_20-(w.shift(20)/w.shift(40)-1)
    f['slope_50']=w.rolling(50).mean().pct_change(10,fill_method=None)
    f['slope_200']=w.rolling(200).mean().pct_change(20,fill_method=None)
    f['volatility_change']=ret.rolling(20).std()/ret.rolling(60).std().replace(0,np.nan)-1
    f['return_skew']=ret.rolling(60).skew()
    f['tail_frequency']=(ret < -.03).astype(float).rolling(60).mean()
    f['volume_acceleration']=f.volume.rolling(5).mean()/f.volume.rolling(20).mean().replace(0,np.nan)-1
    dollars=f.close*f.volume
    f['liquidity_stability']=dollars.rolling(60).std()/dollars.rolling(60).mean().replace(0,np.nan)
    f['gap_mean']=f.gap.rolling(20).mean()
    f['history_count'] = np.arange(1,len(f)+1)
    expected = calendar.sessions(f.iloc[0].session,f.iloc[-1].session)
    indexes = {s:i for i,s in enumerate(expected)}
    positions = f.session.map(indexes)
    f['missing_recent'] = (positions-positions.shift(59)!=59).astype(int)
    f['issue_at'] = f.session.map(lambda s:(calendar.close(s)+pd.Timedelta(minutes=20)).isoformat())
    f['return_1'] = ret
    return f

def build_features(store, symbols, calendar, cutoff, max_rows=250_000, progress=None, cfg=None):
    actions = store.records('actions')
    by_symbol=defaultdict(list)
    for a in actions:
        tickers=set(a.get('resolved_tickers',[])) | {a.get(k) for k in ('symbol','old_symbol','new_symbol','initiating_symbol','target_symbol')}
        for ticker in tickers-{None,''}:
            if applies_to_returns(a,ticker): by_symbol[ticker].append(a)
    frames,errors,breadth = [],{},[]
    total_rows=sum(r[1] for r in store.warehouse_summary() if r[0] in set(symbols))
    stride=max(1,int(np.ceil(total_rows/max_rows)))
    reference=store.bars('SPY')
    benchmark=market_features(reference,by_symbol['SPY'],calendar).set_index('session') if not reference.empty else None
    extra_benchmarks={}
    for ticker in ('QQQ','IWM')+SECTOR_ETFS:
        bb=store.bars(ticker)
        if not bb.empty:
            bb=bb[pd.to_datetime(bb.available_at,utc=True,format='mixed')<=pd.Timestamp(cutoff)]
            if not bb.empty: extra_benchmarks[ticker]=market_features(bb,by_symbol[ticker],calendar).set_index('session')
    master=SecurityMaster(store)
    for index,symbol in enumerate(symbols):
        if progress and index%25==0: progress.update(processed=index)
        try:
            master.resolve(symbol)
        except ValueError as exc:
            errors[symbol]=str(exc)
            continue
        bars = store.bars(symbol)
        if bars.empty:
            errors[symbol] = 'No market history'
            continue
        bars = bars[pd.to_datetime(bars.available_at,utc=True) <= pd.Timestamp(cutoff)]
        if bars.empty:
            continue
        try:
            import hashlib
            from pathlib import Path
            aa=by_symbol[symbol]
            key=digest([symbol,str(bars.iloc[-1].session),len(bars),aa,pd.util.hash_pandas_object(bars[['open','close','volume']],index=False).sum().item(),FEATURE_VERSION])
            cache=store.root/'cache'/('features-'+digest(symbol)[:20]+'.parquet')
            marker=cache.with_suffix('.key')
            if cache.exists() and marker.exists() and marker.read_text()==key:
                f=pd.read_parquet(cache)
            else:
                f=market_features(bars,aa,calendar)
                cache.parent.mkdir(exist_ok=True)
                temporary=cache.with_suffix('.tmp')
                f.to_parquet(temporary,index=False,compression='zstd')
                temporary.replace(cache)
                marker.write_text(key)
            if benchmark is not None:
                r=f.session.map(benchmark.return_1)
                f['market_relative']=f.momentum_20-f.session.map(benchmark.momentum_20)
                f['beta']=f.return_1.rolling(60).cov(r)/r.rolling(60).var().replace(0,np.nan)
            else:
                f['market_relative'],f['beta']=np.nan,np.nan
            for ticker,column in (('QQQ','qqq_relative'),('IWM','iwm_relative')):
                ref=extra_benchmarks.get(ticker)
                f[column]=f.momentum_20-f.session.map(ref.momentum_20) if ref is not None else np.nan
            f['beta_adjusted_momentum']=f.momentum_20-f.beta*f.session.map(benchmark.momentum_20) if benchmark is not None else np.nan
            f['market_volatility']=f.session.map(benchmark.volatility) if benchmark is not None else np.nan
            f['risk_on_off']=f.session.map(extra_benchmarks['QQQ'].momentum_20)-f.session.map(benchmark.momentum_20) if benchmark is not None and 'QQQ' in extra_benchmarks else np.nan
            # Trailing return affinity is a proxy, never asserted historical sector membership.
            correlations={ticker:f.return_1.rolling(126,min_periods=100).corr(f.session.map(extra_benchmarks[ticker].return_1)) for ticker in SECTOR_ETFS if ticker in extra_benchmarks and ticker!=symbol}
            for col in SECTOR_FEATURES: f[col]=np.nan
            if correlations:
                corr=pd.DataFrame(correlations)
                best=corr.fillna(-2).idxmax(axis=1)
                f['sector_proxy_correlation']=corr.max(axis=1)
                for ticker in correlations:
                    mask=(best==ticker)&(f.sector_proxy_correlation>.3)
                    momentum=f.session.map(extra_benchmarks[ticker].momentum_20)
                    f.loc[mask,'sector_proxy_momentum']=momentum[mask]
                    f.loc[mask,'sector_proxy_relative']=f.loc[mask,'momentum_20']-momentum[mask]
            breadth.append(pd.DataFrame({'session':f.session,'market_breadth_50':np.where(f.trend_50.notna(),(f.trend_50>0).astype(float),np.nan),'market_breadth_200':np.where(f.trend_200.notna(),(f.trend_200>0).astype(float),np.nan)}))
            if stride>1 and symbol not in ('SPY','QQQ'):
                mask=np.array([int(hashlib.sha256((symbol+day).encode()).hexdigest()[:12],16)%stride==0 for day in f.session])
                mask[-1]=True
                f=f.loc[mask].copy()
            frames.append(f)
        except UnresolvedAction as exc:
            errors[symbol] = str(exc)
    if not frames:
        raise RuntimeError('FORECASTING BLOCKED: no usable price histories')
    result = pd.concat(frames,ignore_index=True)
    breadth_values=pd.concat(breadth).groupby('session').mean()
    for col in ('market_breadth_50','market_breadth_200'): result[col]=result.session.map(breadth_values[col])
    result['cross_rank'] = result.groupby('session').momentum_20.rank(pct=True)
    # Optional families are constructed by timestamp-safe event joins, and selected only by ablation.
    result = enrich(result,store,cfg)
    result = result.sort_values(['session','symbol']).reset_index(drop=True)
    result['revision_safe']=True
    for correction in store.records('bar_correction'):
        day=pd.Timestamp(correction['session'])
        issues=pd.to_datetime(result.issue_at,utc=True,format='mixed')
        affected=(result.symbol==correction['symbol']) & (pd.to_datetime(result.session)>=day) & (pd.to_datetime(result.session)<=day+pd.Timedelta(days=370)) & (issues<pd.Timestamp(correction['correction_available_at']))
        result.loc[affected,'revision_safe']=False
    result['feature_version'] = FEATURE_VERSION
    if progress: progress.update(processed=len(symbols))
    return result,errors

def enrich(frame, store, cfg=None):
    result = frame.copy()
    news = store.records('news') if cfg is None or cfg.enable_news else []
    filings = store.records('filings', fields=('symbol', 'form')) if cfg is None or cfg.enable_sec else []
    macro = store.records('macro') if cfg is None or cfg.enable_fred else []
    from .fundamentals import FACT_CONCEPTS
    facts = store.records('facts', concepts=FACT_CONCEPTS) if cfg is None or cfg.enable_sec else []
    news_by,filings_by,facts_by=defaultdict(list),defaultdict(list),defaultdict(list)
    for row in news:
        for symbol in set(row.get('symbols',[])): news_by[symbol].append(row)
    for row in filings: filings_by[row.get('symbol')].append(row)
    for row in facts:
        if row.get('namespace')=='us-gaap' and row.get('unit') in ('USD','shares'): facts_by[row.get('symbol')].append(row)
    for name in ADVANCED_PRICE+MARKET_FEATURES+SECTOR_FEATURES:
        if name not in result: result[name]=np.nan
    for name in sum([v for k,v in FAMILIES.items() if k not in ('price','price_advanced','market','sector_proxy')],[]):
        result[name] = np.nan
    for symbol, indices in result.groupby('symbol').groups.items():
        idx = list(indices)
        times = pd.to_datetime(result.loc[idx,'issue_at'],utc=True,format='mixed')
        n = news_by[symbol]
        fs = filings_by[symbol]
        for field, rows, stamp_fn in (
            ('news_count',n,lambda r:r.get('updated_at') or r['created_at']),
            ('news_negative',[r for r in n if any(w in (r.get('headline','')+' '+r.get('summary','')).lower() for w in NEGATIVE)],lambda r:r.get('updated_at') or r['created_at']),
            ('filing_count',fs,lambda r:r['available_at'])):
            if rows:
                stamps = np.sort(pd.to_datetime([stamp_fn(r) for r in rows],utc=True,format='mixed').astype('int64'))
                vals = times.astype('int64').to_numpy()
                counts=(np.searchsorted(stamps,vals,side='right')-np.searchsorted(stamps,vals-30*86400*10**9,side='right')).astype(float)
                counts[vals<stamps[0]+30*86400*10**9]=np.nan
                result.loc[idx,field] = counts
        vals=times.astype('int64').to_numpy()
        if n:
            stamps=np.array([pd.Timestamp(r.get('available_at') or r.get('updated_at') or r['created_at']).value for r in n])
            def counts(mask,days):
                ss=np.sort(stamps[mask]); out=(np.searchsorted(ss,vals,side='right')-np.searchsorted(ss,vals-days*86400*10**9,side='right')).astype(float)
                out[vals<stamps.min()+30*86400*10**9]=np.nan
                return out
            for days in (1,3,7): result.loc[idx,'news_count_'+str(days)]=counts(np.ones(len(n),dtype=bool),days)
            headlines=[(r.get('headline','')+' '+r.get('summary','')).lower() for r in n]
            events={'news_regulatory':('regulator','fda','investigation'), 'news_financing':('offering','dilution','financing'), 'news_earnings':('earnings','guidance'), 'news_ma':('acquisition','merger','takeover')}
            for field,words in events.items(): result.loc[idx,field]=np.minimum(counts(np.array([any(w in t for w in words) for t in headlines]),7),20)
            positive=counts(np.array([any(w in t for w in ('upgrade','beats','raises guidance','approval')) for t in headlines]),7)
            negative=counts(np.array([any(w in t for w in NEGATIVE) for t in headlines]),7)
            result.loc[idx,'news_sentiment']=(positive-negative)/(1+positive+negative)
            result.loc[idx,'news_abnormal']=result.loc[idx,'news_count_7'].to_numpy()/(1+counts(np.ones(len(n),dtype=bool),30)*7/30)
        if fs:
            stamps=np.sort(np.array([pd.Timestamp(r['available_at']).value for r in fs]))
            pos=np.searchsorted(stamps,vals,side='right')-1
            result.loc[idx,'filing_recency']=[(v-stamps[p])/86400e9 if p>=0 else np.nan for v,p in zip(vals,pos)]
            material=np.sort(np.array([pd.Timestamp(r['available_at']).value for r in fs if r.get('form') in ('8-K','8-K/A')],dtype='int64'))
            count=(np.searchsorted(material,vals,side='right')-np.searchsorted(material,vals-30*86400e9,side='right')).astype(float)
            count[vals<stamps[0]]=np.nan
            result.loc[idx,'filing_8k']=count
        ff = facts_by[symbol]
        if ff:
            # Facts with incomparable fiscal durations are excluded from margin/growth estimates.
            for concept,target in [('Assets','assets_log'),('Liabilities','liabilities')]:
                rows = [r for r in ff if r['concept']==concept and not r.get('start')]
                if rows:
                    series = asof_values(times,rows,'val')
                    if target=='assets_log':
                        result.loc[idx,target] = np.log1p(np.maximum(series,0))
                    else:
                        assets = np.expm1(result.loc[idx,'assets_log'].to_numpy())
                        result.loc[idx,'leverage'] = np.divide(series,assets,out=np.full(len(idx),np.nan),where=assets>0)
            # Annual report facts only; revisions keep accession-specific availability.
            revenues = [r for r in ff if r['concept'] in ('RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet') and r.get('start') and 330 <= (pd.Timestamp(r['end'])-pd.Timestamp(r['start'])).days <= 380]
            income = [r for r in ff if r['concept']=='NetIncomeLoss' and r.get('start') and 330 <= (pd.Timestamp(r['end'])-pd.Timestamp(r['start'])).days <= 380]
            if revenues and income:
                margins=[]
                growth=[]
                for rev in revenues:
                    match=next((v for v in income if (v.get('start'),v.get('end'),v.get('accn'))==(rev.get('start'),rev.get('end'),rev.get('accn'))),None)
                    if match and rev['val']:
                        margins.append({'end':rev['end'],'available_at':max(rev['available_at'],match['available_at']),'val':match['val']/rev['val']})
                    prior=[v for v in revenues if v['available_at']<=rev['available_at'] and 330 <= (pd.Timestamp(rev['end'])-pd.Timestamp(v['end'])).days <= 400 and v['val']>0]
                    if prior:
                        previous=max(prior,key=lambda v:(v['end'],v['available_at']))
                        growth.append({'end':rev['end'],'available_at':rev['available_at'],'val':rev['val']/previous['val']-1})
                if margins:
                    result.loc[idx,'profit_margin']=asof_values(times,margins,'val')
                if growth:
                    result.loc[idx,'revenue_growth']=asof_values(times,growth,'val')
    times = pd.to_datetime(result.issue_at,utc=True,format='mixed')
    for series in ('DGS10','DGS2','UNRATE','CPIAUCSL','BAMLC0A0CM'):
        rows = [r for r in macro if r['series']==series]
        if rows:
            result['macro_'+series] = asof_values(times,rows,'value',period='date')
    result['yield_curve']=result['macro_DGS10']-result['macro_DGS2']
    from .fundamentals import enrich_fundamentals
    result=enrich_fundamentals(result,facts)
    from .providers.optional_signals import enrich_optional
    result=enrich_optional(result,store,cfg)
    from .earnings import enrich_earnings
    result=enrich_earnings(result,store,cfg)
    return result

def asof_values(times, rows, value, period='end'):
    # At each availability event, choose the latest economic/fiscal period known then.
    events = sorted(rows,key=lambda r:pd.Timestamp(r['available_at']).value)
    state = {}
    timeline = []
    values = []
    for r in events:
        state[r.get(period,'')] = float(r[value])
        timeline.append(pd.Timestamp(r['available_at']).value)
        values.append(state[max(state)])
    pos = np.searchsorted(np.asarray(timeline),pd.to_datetime(times,utc=True,format='mixed').astype('int64'),side='right')-1
    return np.array([values[p] if p>=0 else np.nan for p in pos])

def make_labels(features, store, calendar, horizons, friction, progress=None):
    frames = []
    actions = store.records('actions')
    for index,(symbol,g) in enumerate(features.groupby('symbol')):
        if progress: progress.update(processed=index)
        try:
            SecurityMaster(store).resolve(symbol)
        except ValueError:
            continue
        bars = store.bars(symbol).set_index('session')
        aa = relevant(actions,symbol)
        # A finished symbol's expensive labels are reusable after interruption. The key
        # includes all input features, prices, actions and label parameters, never just dates.
        key=digest([g.to_json(orient='split',date_format='iso'),bars.to_json(orient='split',date_format='iso'),aa,list(horizons),friction,'labels-v1'])
        cache=store.root/'cache'/('labels-'+digest(symbol)[:20]+'.parquet')
        marker=cache.with_suffix('.key')
        if cache.exists() and marker.exists() and marker.read_text()==key:
            frames.append(pd.read_parquet(cache))
            continue
        output = []
        for row in g.to_dict('records'):
            if not row.get('revision_safe',True):
                continue
            entry = calendar.next_open(row['issue_at'])
            if entry not in bars.index:
                continue
            for h in horizons:
                end = calendar.settle(entry,h)
                if end not in bars.index:
                    continue
                try:
                    y = total_return(float(bars.loc[entry,'open']),float(bars.loc[end,'close']),entry,end,aa,friction)
                except UnresolvedAction:
                    continue
                output.append({**row,'horizon':h,'entry_session':entry,'label_end':end,'label_available_at':calendar.close(end).isoformat(),'target':y})
        cache.parent.mkdir(parents=True,exist_ok=True)
        temporary=cache.with_suffix('.tmp')
        symbol_frame = pd.DataFrame(output)
        symbol_frame.to_parquet(temporary,index=False)
        frames.append(symbol_frame)
        temporary.replace(cache)
        marker_tmp=marker.with_suffix('.tmp-key')
        marker_tmp.write_text(key)
        marker_tmp.replace(marker)
    if progress: progress.update(processed=features.symbol.nunique())
    frames = [f for f in frames if not f.empty]
    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not result.empty:
        for symbol in ('SPY','QQQ'):
            benchmark=result[result.symbol==symbol].set_index(['session','horizon']).target
            result['benchmark_'+symbol]=[benchmark.get((s,h),np.nan) for s,h in zip(result.session,result.horizon)]
    return result

