"""Accession-matched annual fundamentals. No interpolation across unknown filings."""
from collections import defaultdict
import pandas as pd
import numpy as np

# The only us-gaap concepts any feature or report reads. Loading or storing others wastes memory.
FACT_CONCEPTS = ('Assets', 'Liabilities', 'NetIncomeLoss', 'Revenues', 'SalesRevenueNet', 'OperatingIncomeLoss',
                 'RevenueFromContractWithCustomerExcludingAssessedTax', 'CashAndCashEquivalentsAtCarryingValue',
                 'NetCashProvidedByUsedInOperatingActivities', 'PaymentsToAcquirePropertyPlantAndEquipment',
                 'CommonStockSharesOutstanding')


def enrich_fundamentals(frame,facts):
    from .features import asof_values
    grouped=defaultdict(list)
    for r in facts:
        if r.get('namespace')=='us-gaap':grouped[r.get('symbol')].append(r)
    for symbol,indices in frame.groupby('symbol').groups.items():
        rows=grouped[symbol];events=defaultdict(list)
        indexed=defaultdict(list)
        for v in rows:indexed[(v.get('accn'),v.get('end'),v.get('concept'),v.get('unit'))].append(v)
        def annual(r):
            return r.get('start') and 330<=(pd.Timestamp(r['end'])-pd.Timestamp(r['start'])).days<=380
        def matches(row,concepts,unit='USD'):
            return [v for concept in concepts for v in indexed[(row.get('accn'),row.get('end'),concept,unit)]]
        for r in rows:
            value=None;name=None
            if r.get('unit')=='USD' and r.get('concept')=='CashAndCashEquivalentsAtCarryingValue':
                a=matches(r,('Assets',))
                if a and a[0]['val']>0:value=r['val']/a[0]['val'];name='cash_assets'
            elif r.get('concept')=='OperatingIncomeLoss' and r.get('unit')=='USD' and annual(r):
                a=[v for v in matches(r,('RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet')) if v.get('start')==r.get('start')]
                if a and a[0]['val']>0:value=r['val']/a[0]['val'];name='operating_margin'
            elif r.get('concept')=='NetCashProvidedByUsedInOperatingActivities' and r.get('unit')=='USD' and annual(r):
                a=matches(r,('Assets',));cap=[v for v in matches(r,('PaymentsToAcquirePropertyPlantAndEquipment',)) if v.get('start')==r.get('start')]
                if a and cap and a[0]['val']>0:value=(r['val']-cap[0]['val'])/a[0]['val'];name='free_cash_flow_assets'
            elif r.get('concept')=='CommonStockSharesOutstanding' and r.get('unit')=='shares':
                a=[v for v in rows if v.get('concept')==r['concept'] and v.get('unit')=='shares' and v.get('available_at')<=r['available_at'] and 330<=(pd.Timestamp(r['end'])-pd.Timestamp(v['end'])).days<=400 and v['val']>0]
                if a:
                    previous=max(a,key=lambda v:(v['end'],v['available_at']));value=r['val']/previous['val']-1;name='shares_growth'
            if value is not None and np.isfinite(value):
                # Matched accession is public no earlier than all participating facts.
                available=max([r['available_at']]+[v['available_at'] for v in a]+[v['available_at'] for v in (cap if name=='free_cash_flow_assets' else [])])
                events[name].append({'available_at':available,'end':r['end'],'value':value})
        for name,values in events.items():frame.loc[indices,name]=asof_values(frame.loc[indices,'issue_at'],values,'value')
    return frame
