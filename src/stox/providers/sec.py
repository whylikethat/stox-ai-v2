from __future__ import annotations
import pandas as pd
import json
import os
import time
from ..storage import digest, encode
from .http import HTTP, ProviderError

from ..fundamentals import FACT_CONCEPTS

class SEC:
    def __init__(self, user_agent, http=None):
        if not user_agent or '@' not in user_agent:
            raise ProviderError('SEC_USER_AGENT must identify the application and a contact email')
        self.headers = {'User-Agent':user_agent,'Accept-Encoding':'gzip, deflate'}
        self.http = http or HTTP()

    def get(self, url):
        return self.http.get(url, headers=self.headers, interval=.6)

    def mappings(self):
        raw = self.get('https://www.sec.gov/files/company_tickers.json')
        return {r['ticker']:str(r['cik_str']).zfill(10) for r in raw.values()}

    def company(self, cik, store=None, snapshot=None):
        def cached(url, historical=False):
            if store is None: return self.get(url)
            directory=store.root/'cache'/'sec'
            directory.mkdir(parents=True,exist_ok=True)
            # One path per URL. Reuse pages of an interrupted frozen snapshot; historical
            # submission files revalidate at least every 30 days. Never backdate observations.
            path=directory/(digest(url)+'.json')
            if path.exists():
                try:
                    content=json.loads(path.read_text())
                    if content['snapshot']==snapshot or (historical and time.time()-path.stat().st_mtime<30*86400):
                        return content['payload']
                except (ValueError,KeyError): pass
            raw=self.get(url)
            temporary=path.with_suffix('.tmp')
            with temporary.open('w') as file:
                file.write(encode({'snapshot':snapshot,'payload':raw}))
                file.flush(); os.fsync(file.fileno())
            temporary.replace(path)
            return raw
        submissions = cached(f'https://data.sec.gov/submissions/CIK{cik}.json')
        groups = [submissions.get('filings',{}).get('recent',{})]
        for file in submissions.get('filings',{}).get('files',[]):
            groups.append(cached('https://data.sec.gov/submissions/' + file['name'],historical=True))
        filings = []
        for raw in groups:
            for i, acc in enumerate(raw.get('accessionNumber',[])):
                row = {k:v[i] for k,v in raw.items() if isinstance(v,list) and len(v)>i}
                accepted = row.get('acceptanceDateTime')
                if accepted:
                    stamp = pd.Timestamp(accepted)
                    if stamp.tzinfo is None:
                        stamp = stamp.tz_localize('America/New_York',ambiguous='raise',nonexistent='raise')
                else:
                    # Date-only filing metadata is not assumed available at midnight that day.
                    stamp = (pd.Timestamp(row['filingDate'])+pd.Timedelta(days=1)).tz_localize('America/New_York')
                row.update(cik=cik,available_at=stamp.tz_convert('UTC').isoformat(),provider='sec')
                filings.append(row)
        try:
            facts = cached(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json')
        except ProviderError as exc:
            # Some issuers (trusts, some foreign filers, new registrants) have submissions but no XBRL
            # company-facts document. That is a property of the issuer, not a provider outage, so it must
            # not abort the refresh of every other issuer. Other errors still propagate.
            if exc.status_code != 404:
                raise
            facts = {}
        by_acc = {r['accessionNumber']:r['available_at'] for r in filings}
        values = []
        for namespace, fields in facts.get('facts',{}).items():
            if namespace != 'us-gaap':
                continue
            for concept, detail in fields.items():
                if concept not in FACT_CONCEPTS:
                    continue
                for unit, observations in detail.get('units',{}).items():
                    if unit not in ('USD', 'shares'):
                        continue
                    for r in observations:
                        stamp = by_acc.get(r.get('accn'))
                        if stamp is None and r.get('filed'):
                            stamp = (pd.Timestamp(r['filed'])+pd.Timedelta(days=1)).tz_localize('America/New_York').tz_convert('UTC').isoformat()
                        if stamp:
                            values.append({**r,'cik':cik,'concept':concept,'namespace':namespace,'unit':unit,'available_at':stamp,'provider':'sec'})
        return filings, values
