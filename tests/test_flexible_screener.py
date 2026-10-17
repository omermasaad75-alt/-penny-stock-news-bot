"""عقد الماسح: حدود الفلاتر الصارمة، واستقلال الإشارات والنقاط عن الأهلية."""
import numpy as np
import pandas as pd
import pytest

from app.analytics.technical_score import technical_components
from app.config import StrategyParams
from app.providers.base import ReverseSplitEvent, ShortData
from app.providers.hub import DataHub
from app.strategy import engine as eng


@pytest.mark.parametrize('shares,eligible', [(0, True), (19999, True), (19999.9, True),
                                            (20000, False), (20001, False),
                                            (None, False), (-1, False), (float('nan'), False)])
def test_short_shares_exclusive_boundary(monkeypatch, shares, eligible):
    hub = DataHub(mode='demo')
    monkeypatch.setattr(hub, 'short_data', lambda *a: ShortData(
        symbol='JZ', short_shares=shares, short_pct_float=0.001, short_volume_ratio=0.01))
    a = eng.analyze_symbol('JZ', hub)
    assert a.eligible is eligible
    if not eligible:
        assert set(a.rejection_codes) <= {'short_shares', 'missing_short_shares'}


def test_missing_short_record_is_not_zero(monkeypatch):
    hub = DataHub(mode='demo')
    monkeypatch.setattr(hub, 'short_data', lambda *a: None)
    a = eng.analyze_symbol('JZ', hub)
    assert not a.eligible and 'missing_short_shares' in a.rejection_codes


@pytest.mark.parametrize('price,expected', [(0.99999, False), (1, True), (5, True), (5.00001, False)])
def test_price_inclusive_without_rounding(monkeypatch, price, expected):
    hub = DataHub(mode='demo')
    df = hub.daily('JZ', 320).copy()
    df.loc[df.index[-1], 'close'] = price
    df.loc[df.index[-1], 'high'] = max(df['high'].iloc[-1], price)
    df.loc[df.index[-1], 'low'] = min(df['low'].iloc[-1], price)
    monkeypatch.setattr(hub, 'daily', lambda *a: df)
    a = eng.analyze_symbol('JZ', hub)
    assert ('price' not in a.rejection_codes) is expected


@pytest.mark.parametrize('days,valid', [(0, True), (60, True), (61, False), (-1, False)])
def test_calendar_split_boundary(days, valid):
    now = pd.Timestamp.now().normalize()
    event = ReverseSplitEvent('X', now - pd.Timedelta(days=days), factor=120)
    picked, _ = eng.pick_reverse_split([event], StrategyParams(), now)
    assert (picked is not None) is valid


def test_no_patterns_or_liquidity_do_not_reject(monkeypatch):
    hub = DataHub(mode='demo')
    monkeypatch.setattr(eng.pat, 'detect_all', lambda *a: [])
    # Optional thresholds cannot add a sixth hard filter.
    params = StrategyParams(min_avg_dollar_volume=1e15, max_float_shares=1)
    a = eng.analyze_symbol('JZ', hub, params)
    assert a.eligible
    assert next(x for x in a.breakdown if x['key'] == 'patterns')['points'] == 0


def test_optional_pattern_failure_keeps_eligible(monkeypatch):
    hub = DataHub(mode='demo')
    def unavailable(*args):
        raise ValueError('unavailable')
    monkeypatch.setattr(eng.pat, 'detect_all', unavailable)
    assert eng.analyze_symbol('JZ', hub).eligible


def blank_frame():
    return pd.DataFrame({'low': [10.] * 60, 'close': [9.] * 60,
                         'volume': [np.nan] * 60})


def test_zero_score_and_or_pattern_scoring():
    df = blank_frame()
    params = StrategyParams()
    base = technical_components(df, [], params)
    assert sum(p[2] * p[3] / 100 for p in base) == 0
    for name in ('double_bottom', 'falling_wedge', 'rsi_divergence'):
        components = technical_components(df, [{'name': name, 'bias': 'bullish',
            'confidence': 1, 'status': 'confirmed'}], params)
        assert sum(p[2] * p[3] / 100 for p in components) == 35


def test_score_components_bounded_and_ema30_used():
    df = blank_frame()
    df['volume'] = 1000.
    df.loc[df.index[-3:], 'volume'] = 100.
    df['close'] = 10.
    for period in (20, 30, 50):
        df[f'ema{period}'] = np.linspace(8, 9, len(df))
    parts = technical_components(df, [], StrategyParams())
    assert next(p for p in parts if p[0] == 'ema')[2] == 100
    for part in parts:
        assert 0 <= part[2] <= 100


def test_sort_is_score_first_even_for_early_stage(monkeypatch):
    rows = {'EARLY': eng.Analysis('EARLY', eligible=True, score=80, phase=3),
            'READY': eng.Analysis('READY', eligible=True, score=10, phase=6),
            'ZERO': eng.Analysis('ZERO', eligible=True, score=0, phase=1)}
    monkeypatch.setattr(eng, 'analyze_symbol', lambda symbol, *a: rows[symbol])
    out = eng.scan(list(rows), None)
    assert [r.symbol for r in out] == ['EARLY', 'READY', 'ZERO']


def test_rsi_only_during_bottom_formation():
    from app.analytics.indicators import add_indicators
    full = pd.DataFrame({'open': [2.] * 60, 'high': [2.1] * 60,
        'low': [1.9] * 60, 'close': [2.] * 60, 'volume': [100.] * 60},
        index=pd.bdate_range('2026-01-01', periods=60))
    full = add_indicators(full)
    full['rsi'] = 40.
    full.iloc[21, full.columns.get_loc('rsi')] = 30.
    full.iloc[-1, full.columns.get_loc('rsi')] = 20.
    mom = eng.analyse_momentum(full, {'peak_pos': 20, 'bottom_pos': 22, 'base_end_pos': 25}, StrategyParams())
    assert mom['rsi_trough'] == 30. # later oversold must not satisfy core rule
    full.iloc[21, full.columns.get_loc('rsi')] = 29.9999
    mom = eng.analyse_momentum(full, {'peak_pos': 20, 'bottom_pos': 22, 'base_end_pos': 25}, StrategyParams())
    assert mom['rsi_trough'] < 30


def test_post_split_peak_not_limited_to_first_five_sessions():
    idx = pd.bdate_range('2026-01-01', periods=20)
    df = pd.DataFrame({'open': 3., 'high': 3.1, 'low': 2.8,
                       'close': 3., 'volume': 100.}, index=idx)
    df.loc[idx[12], 'high'] = 5.
    df.loc[idx[12], 'low'] = 4.
    df.loc[idx[12], 'close'] = 4.2
    df.loc[idx[16], 'low'] = 3.5
    # No lower lows after the actual peak.
    df.loc[idx[13:], 'low'] = 3.5
    df.loc[idx[13:], 'high'] = 4.
    df.loc[idx[13:], 'close'] = 3.7
    event = ReverseSplitEvent('X', idx[0], factor=8.)
    result = eng.analyse_structure(df, event, StrategyParams())
    assert result['peak_pos'] == 12
    assert result['decline_pct'] == pytest.approx(-30)


def test_liquidity_sweep_scores_without_full_lifecycle():
    df = blank_frame()
    df['close'] = 10.1
    df.loc[df.index[-1], 'low'] = 9.8
    parts = technical_components(df, [], StrategyParams())
    support = next(p for p in parts if p[0] == 'support')
    assert support[2] == 100 # independent test and sweep, no impulse prerequisite


def test_eligible_stock_with_zero_score_still_in_main_cli_table(monkeypatch, capsys):
    from app.scanner import print_table
    monkeypatch.setattr(eng, 'technical_components', lambda *a, **kw: [
        ('patterns', 'النماذج الفنية', 0, 100, 'غير متاح')])
    a = eng.analyze_symbol('JZ', DataHub(mode='demo'))
    assert a.eligible and a.score == 0
    print_table([a])
    assert 'JZ' in capsys.readouterr().out


def test_missing_optional_profile_does_not_hide_eligible(monkeypatch):
    hub = DataHub(mode='demo')
    monkeypatch.setattr(hub, 'profile', lambda *a: None)
    assert eng.analyze_symbol('JZ', hub).eligible
