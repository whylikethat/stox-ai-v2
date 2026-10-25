import pandas as pd
from stox.alerts import add_alerts
from stox.config import Config
from stox.fundamentals import FACT_CONCEPTS
from stox.providers.sec import SEC


def add(store, kind, record_id, payload, available_at='2025-01-02T00:00:00+00:00'):
    store.record(kind, record_id, payload, available_at)


def test_records_filter_by_concept_inside_sqlite(store):
    add(store, 'facts', 'a', {'symbol': 'AAA', 'concept': 'Assets', 'val': 1})
    add(store, 'facts', 'b', {'symbol': 'AAA', 'concept': 'ObscureConcept', 'val': 2})
    add(store, 'facts', 'c', {'symbol': 'BBB', 'concept': 'Liabilities', 'val': 3})
    got = store.records('facts', concepts=('Assets', 'Liabilities'))
    assert sorted(r['concept'] for r in got) == ['Assets', 'Liabilities']
    assert len(store.records('facts')) == 3


def test_records_filter_by_form_symbol_and_cutoff(store):
    add(store, 'filings', 'f1', {'symbol': 'AAA', 'form': '8-K', 'items': '4.02'}, '2025-01-02T00:00:00+00:00')
    add(store, 'filings', 'f2', {'symbol': 'AAA', 'form': '4'}, '2025-01-03T00:00:00+00:00')
    add(store, 'filings', 'f3', {'symbol': 'BBB', 'form': '8-K'}, '2025-01-04T00:00:00+00:00')
    got = store.records('filings', '2025-01-10T00:00:00+00:00', forms=('8-K', '8-K/A'), symbol='AAA')
    assert [r['items'] for r in got] == ['4.02']
    assert store.records('filings', '2025-01-02T12:00:00+00:00', forms=('8-K',), symbol='BBB') == []


def test_field_projection_returns_only_requested_fields(store):
    add(store, 'filings', 'f1', {'symbol': 'AAA', 'form': '8-K', 'items': '2.02', 'big': 'x' * 1000})
    row = store.records('filings', fields=('symbol', 'form'))[0]
    assert set(row) == {'available_at', 'symbol', 'form'} and row['symbol'] == 'AAA'


def test_recent_by_symbol_keeps_newest_per_symbol_in_time_order(store):
    for i in range(10):
        add(store, 'filings', f'a{i}', {'symbol': 'AAA', 'n': i}, f'2025-01-{i + 1:02d}T00:00:00+00:00')
    for i in range(2):
        add(store, 'filings', f'b{i}', {'symbol': 'BBB', 'n': i}, f'2025-02-{i + 1:02d}T00:00:00+00:00')
    got = store.recent_by_symbol('filings', per_symbol=3)
    assert [r['n'] for r in got if r['symbol'] == 'AAA'] == [7, 8, 9]
    assert len([r for r in got if r['symbol'] == 'BBB']) == 2


class SeqHTTP:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, url, **kwargs):
        return self.responses.pop(0)


def test_sec_stores_only_concepts_the_model_reads():
    submissions = {'filings': {'recent': {'accessionNumber': ['acc-1'], 'acceptanceDateTime': ['2025-01-02T15:00:00Z'],
                                          'filingDate': ['2025-01-02'], 'form': ['10-K']}, 'files': []}}
    obs = [{'accn': 'acc-1', 'end': '2024-12-31', 'val': 5, 'filed': '2025-01-02'}]
    facts = {'facts': {'us-gaap': {'Assets': {'units': {'USD': obs, 'pure': obs}},
                                   'Unrelated': {'units': {'USD': obs}}},
                       'dei': {'EntityCommonStockSharesOutstanding': {'units': {'shares': obs}}}}}
    filings, values = SEC('Test test@example.com', SeqHTTP([submissions, facts])).company('0000000001')
    assert [(v['concept'], v['unit'], v['namespace']) for v in values] == [('Assets', 'USD', 'us-gaap')]
    assert 'Assets' in FACT_CONCEPTS and len(filings) == 1


class RecordingStore:
    def __init__(self, filings):
        self.filings, self.calls = filings, []

    def records(self, kind, cutoff=None, **filters):
        self.calls.append((kind, filters))
        return self.filings

    def bars(self, symbol):
        return pd.DataFrame()


def test_alerts_query_8k_directly_not_the_last_six_filings(tmp_path):
    cfg = Config(tmp_path / 's')
    old_8k = [{'form': '8-K', 'items': '3.01', 'available_at': '2025-03-01T00:00:00+00:00', 'filingDate': '2025-03-01'}]
    holding = {'symbol': 'AAA', 'purchased': '2025-01-01', 'resolution_status': 'resolved', 'status': 'HEALTHY', 'reason': '',
               'current_forecasts': [], 'evidence': {'filings': []}}
    store = RecordingStore(old_8k)
    add_alerts([holding], cfg, store, {}, '2025-03-10T00:00:00+00:00')
    assert store.calls[0][1] == {'forms': ('8-K', '8-K/A'), 'symbol': 'AAA'}
    assert [a['kind'] for a in holding['alerts']] == ['EVENT']


def test_filtered_load_uses_a_fraction_of_the_memory(store):
    import json
    import tracemalloc
    rows = []
    for i in range(6000):
        concept = FACT_CONCEPTS[i % 11] if i % 10 == 0 else f'Other{i % 400}'
        payload = {'symbol': f'S{i % 50}', 'concept': concept, 'val': i, 'accn': f'acc-{i}', 'end': '2024-12-31', 'form': '10-K',
                   'filed': '2025-01-02', 'fy': 2024, 'fp': 'FY', 'unit': 'USD', 'namespace': 'us-gaap'}
        rows.append(('facts', f'r{i}', '0', '2025-01-02T00:00:00+00:00', '2025-02-01T00:00:00+00:00', json.dumps(payload)))
    store.db.executemany('INSERT INTO records VALUES(?,?,?,?,?,?)', rows)
    store.db.commit()

    def peak(**kwargs):
        tracemalloc.start()
        store.records('facts', **kwargs)
        _, high = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return high
    assert peak(concepts=FACT_CONCEPTS) < peak() * .4
