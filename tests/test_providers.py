import pytest
from stox.providers.alpaca import Alpaca,parse_bar
from stox.providers.http import HTTP,ProviderError
from stox.providers.alpha import AlphaVantage
from stox.providers.fred import FRED
from stox.providers.sec import SEC
from stox.ingestion import Ingestor

class FakeHTTP:
    def __init__(self,values):
        self.values=iter(values)
        self.calls=[]
    def get(self,url,**kwargs):
        self.calls.append((url,kwargs))
        value=next(self.values)
        if isinstance(value,Exception):
            raise value
        return value

def alpaca(http):
    return Alpaca({'ALPACA_API_KEY':'fake','ALPACA_API_SECRET':'fake'},http)

def test_news_pagination():
    http=FakeHTTP([{'news':[{'id':1}],'next_page_token':'next'},{'news':[{'id':2}],'next_page_token':None}])
    assert len(list(alpaca(http).news('2025-01-01','2025-01-10')))==2
    assert http.calls[1][1]['params']['page_token']=='next'

def test_repeated_token():
    h=FakeHTTP([{'news':[],'next_page_token':'a'}]*3)
    with pytest.raises(ProviderError):
        list(alpaca(h).news('a','b'))

def test_raw_sip_bar_parsing(calendar):
    r=parse_bar('X',{'t':'2025-01-06T05:00:00Z','o':10,'h':12,'l':9,'c':11,'v':100},calendar,'2025-01-07T00:00:00Z')
    assert r['session']=='2025-01-06'
    assert r['close']==11
    with pytest.raises(ProviderError):
        parse_bar('X',{'t':'2025-01-06T05:00:00Z','o':10,'h':1,'l':9,'c':11,'v':100},calendar,'x')

def test_partial_failure_does_not_advance(cfg,store,calendar):
    class Partial:
        def assets(self):
            return [{'id':'1','symbol':'X','name':'X Common Stock','class':'us_equity','tradable':True,'exchange':'NASDAQ','status':'active'}]
        def bars(self,*args):
            yield {'X':[{'t':'2025-01-06T05:00:00Z','o':10,'h':12,'l':9,'c':11,'v':100}]}
            raise ProviderError('failure on page 2')
    with pytest.raises(ProviderError):
        Ingestor(cfg,store,calendar,Partial()).run('2025-01-07T22:00:00Z')
    assert store.checkpoint('bars:QQQ') is None

def test_retry_and_redacted_errors():
    class Response:
        headers={}
        text='secret'
        def __init__(self,status): self.status_code=status
        def json(self): return {'ok':True}
    class Session:
        def __init__(self,statuses): self.statuses=iter(statuses)
        def get(self,*a,**k): return Response(next(self.statuses))
    sleeps=[]
    assert HTTP(Session([429,500,200]),sleeps.append).get('https://example.com')=={'ok':True}
    with pytest.raises(ProviderError,match='HTTP 403') as e:
        HTTP(Session([403]),sleeps.append).get('https://example.com?key=secret')
    assert 'secret' not in str(e.value)

def test_alpha_budget(store):
    av=AlphaVantage('fake',store,1,FakeHTTP(['symbol,reportDate\nX,2025-01-01\n']))
    assert av.earnings()[0]['symbol']=='X'
    with pytest.raises(ProviderError,match='budget'):
        av.earnings()

def test_fred_vintage_and_missing_values():
    h=FakeHTTP([{'count':2,'observations':[{'date':'2024-01-01','realtime_start':'2025-01-02','value':'1.5'},{'date':'2024-02-01','realtime_start':'2025-01-02','value':'.'}]}])
    rows=list(FRED('fake',h).observations('TEST','2020-01-01','2025-01-10'))[0]
    assert len(rows)==1
    assert rows[0]['available_at'].startswith('2025-01-03')

def test_sec_available_time_and_facts():
    raw={'filings':{'recent':{'accessionNumber':['abc'],'acceptanceDateTime':['2025-01-02T15:00:00Z'],'filingDate':['2025-01-02'],'form':['10-K']},'files':[]}}
    facts={'facts':{'us-gaap':{'Assets':{'units':{'USD':[{'val':100,'accn':'abc','filed':'2025-01-02','end':'2024-12-31'}]}}}}}
    h=FakeHTTP([raw,facts])
    filings,values=SEC('Test test@example.com',h).company('0000000001')
    assert filings[0]['available_at']=='2025-01-02T15:00:00+00:00'
    assert values[0]['available_at']==filings[0]['available_at']
    assert h.calls[0][1]['interval']==.6


def test_sec_issuer_without_xbrl_facts_is_not_an_outage():
    raw={'filings':{'recent':{'accessionNumber':['abc'],'acceptanceDateTime':['2025-01-02T15:00:00Z'],'filingDate':['2025-01-02'],'form':['8-K']},'files':[]}}
    h=FakeHTTP([raw,ProviderError('data.sec.gov: HTTP 404',status_code=404)])
    filings,values=SEC('Test test@example.com',h).company('0001041130')
    assert len(filings)==1 and values==[]

def test_sec_non_404_facts_error_still_raises():
    raw={'filings':{'recent':{'accessionNumber':['abc'],'acceptanceDateTime':['2025-01-02T15:00:00Z'],'filingDate':['2025-01-02'],'form':['8-K']},'files':[]}}
    h=FakeHTTP([raw,ProviderError('data.sec.gov: HTTP 503',status_code=503)])
    with pytest.raises(ProviderError):
        SEC('Test test@example.com',h).company('0000000001')
