import copy,json
import numpy as np
import pandas as pd
import pytest
from stox.actions import relevant,total_return,UnresolvedAction
from stox.ranking import rank
from stox.reporting import compact_report,evidence_for,write_reports,research_status


@pytest.mark.parametrize('action_type',['cash_mergers','stock_mergers','stock_and_cash_mergers'])
def test_merger_acquirer_keeps_price_return_target_stays_blocked(action_type):
    a=dict(action_type=action_type,acquirer_symbol='BUY',acquiree_symbol='SELL',effective_date='2025-02-01',resolved_tickers=['BUY','SELL'])
    assert total_return(100,110,'2025-01-01','2025-03-01',relevant([a],'BUY'),0)==pytest.approx(.1)
    assert relevant([a],'BUY',for_returns=False)==[a]
    with pytest.raises(UnresolvedAction):total_return(100,110,'2025-01-01','2025-03-01',relevant([a],'SELL'),0)
    ambiguous=dict(a,acquiree_symbol='BUY')
    assert relevant([ambiguous],'BUY')==[ambiguous]
    missing=dict(a,acquiree_symbol=None)
    assert relevant([missing],'BUY')==[missing]
    conflicting=dict(a,acquirer_cusip='123456789',acquiree_cusip='123456789')
    assert relevant([conflicting],'BUY')==[conflicting]


def test_action_resolution_requires_ticker_role_not_identifier_text():
    a=dict(action_type='cash_mergers',acquiree_symbol='SELL',resolved_tickers=['BUY'],effective_date='2025-02-01',security_references=[dict(role='acquirer_symbol',kind='cusip',value='BUY',market_data_symbol=None)])
    assert relevant([a],'BUY')==[a]
    a['security_references']=[dict(role='acquirer_symbol',kind='ticker',value='OLD',market_data_symbol='BUY')]
    assert relevant([a],'BUY')==[]
    a['security_references'].append(dict(role='acquiree_symbol',kind='ticker',market_data_symbol='BUY'))
    assert relevant([a],'BUY')==[a]


def test_stock_dividend_multiplier_and_ex_date_cash():
    a=dict(action_type='stock_dividends',symbol='CBSH',rate=1.05,ex_date='2025-02-01')
    assert total_return(105,100,'2025-01-01','2025-03-01',[a],0)==pytest.approx(0)
    assert total_return(100,100,'2025-02-01','2025-03-01',[a],0)==0
    cash=dict(action_type='cash_dividends',symbol='CBSH',rate=2,ex_date='2025-02-02')
    assert total_return(105,100,'2025-01-01','2025-03-01',[cash,a],0)==pytest.approx(.02)
    for rate in (None,0,-1,.05,float('nan'),float('inf')):
        with pytest.raises(UnresolvedAction):total_return(105,100,'2025-01-01','2025-03-01',[dict(a,rate=rate)],0)


def test_unknown_actions_still_block():
    for kind in ('spin_offs','unit_splits','rights_distributions'):
        a=dict(action_type=kind,symbol='ABC',effective_date='2025-02-01')
        with pytest.raises(UnresolvedAction):total_return(100,101,'2025-01-01','2025-03-01',relevant([a],'ABC'),0)


def candidate(symbol,p,exclusions=None):
    return dict(symbol=symbol,horizon=30,p_gain=p,expected_return=.03,median_return=.02,p_loss_5=.1,p_loss_10=.05,p_loss_20=.01,qualified=False,model_agreement='high',consistency_status='no detected conflict',exclusions=exclusions or [],session='2025-01-01')


def test_data_adequacy_precedes_probability_preserves_forecasts():
    rows=[candidate('BLLN',.9,['insufficient history']),candidate('STALE',.95,['stale price history']),candidate('OK',.6)]
    before=copy.deepcopy(rows);out=rank(rows)
    assert out[0]['symbol']=='OK' and len(out)==3 and rows==before
    assert {r['symbol']:r['p_gain'] for r in out}=={r['symbol']:r['p_gain'] for r in rows}
    assert research_status(out[1])=='RESEARCH — DATA LIMITED'
    assert rank(list(reversed(rows)))==out


def test_compact_evidence_roundtrip_and_exports(tmp_path):
    context={'news':[{'headline':'EVIDENCE_SENTINEL'+'X'*10000}],'corporate_actions':[]}
    rows=rank([dict(candidate('ABC',.6),catalyst_context=context)])
    report=dict(run_id='correction',metadata={},warnings=[],primary_30d=rows,across_horizons=rows,by_horizon={'30':rows},research_candidates=rows,details=[dict(symbol='ABC',forecasts=rows,evidence=context)],holdings=[],prospective={},backtests={},validation={})
    original=copy.deepcopy(report);small=compact_report(report)
    assert report==original and compact_report(small)==small
    assert json.dumps(small).count('EVIDENCE_SENTINEL')==1
    assert len(json.dumps(small))<len(json.dumps(report))/3
    for row in small['research_candidates']+small['primary_30d']+small['details']:
        assert evidence_for(small,row)==context
    assert evidence_for(report,rows[0])==context
    path=write_reports(tmp_path,report)
    for name in ['report.json','report.md','ai_review.md']:
        assert (path/name).read_text().count('EVIDENCE_SENTINEL')==1
    assert 'EVIDENCE_SENTINEL' not in (path/'report.html').read_text()
    assert json.loads((path/'report.json').read_text())['report_schema_version']==2


def test_feature_build_uses_role_aware_actions(store,cfg,calendar,monkeypatch):
    from stox.features import build_features
    from stox.security import SecurityMaster
    from types import SimpleNamespace
    days=calendar.sessions('2024-01-02','2025-01-03');x=np.arange(len(days))
    frame=pd.DataFrame(dict(symbol='BUY',session=days,open=100+x/10,close=100+x/10,volume=1e6,available_at=[calendar.close(d).isoformat() for d in days]))
    a=dict(action_type='cash_mergers',acquirer_symbol='BUY',acquiree_symbol='SELL',effective_date='2024-06-03',resolved_tickers=['BUY','SELL'])
    monkeypatch.setattr(store,'records',lambda kind,*args,**kwargs:[a] if kind=='actions' else [])
    monkeypatch.setattr(store,'bars',lambda s:frame.copy() if s=='BUY' else pd.DataFrame())
    monkeypatch.setattr(store,'warehouse_summary',lambda:[('BUY',len(frame),days[-1])])
    monkeypatch.setattr(SecurityMaster,'resolve',lambda self,s:SimpleNamespace(ticker=s))
    f,errors=build_features(store,['BUY'],calendar,'2025-01-04T00:00:00Z',cfg=cfg)
    assert not errors and f.symbol.unique().tolist()==['BUY']
    assert f.iloc[-1].wealth==pytest.approx(frame.iloc[-1].close)
