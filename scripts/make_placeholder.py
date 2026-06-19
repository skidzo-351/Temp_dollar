#!/usr/bin/env python3
"""초기 배포용 placeholder data.json 생성 (Action이 첫 실행되면 실제 데이터로 교체됨)"""
import json
import sys
import os
import random
import datetime as dt

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from update_data import calculate_signal_series, backtest_portfolio, compute_max_drawdown

random.seed(7)
closes = [400.0]
for i in range(800):
    closes.append(closes[-1] * (1 + random.gauss(0.0003, 0.011)))

dates = []
d = dt.date.today() - dt.timedelta(days=len(closes))
for i in range(len(closes)):
    dates.append(str(d + dt.timedelta(days=i)))

trades, states = calculate_signal_series(closes)
port = backtest_portfolio(states, dates)
mdd = compute_max_drawdown(port)
bh = [100.0]
for _ in range(1, len(states)):
    bh.append(bh[-1] * (1 + 0.80 * 0.035 / 252))

latest = states[-1]
div = latest['divergence']
pos = latest['position']
signal = 'WAITING'
if pos == 0.0 and div is not None and div <= -5:
    signal = 'ENTRY1_READY'
elif pos > 0 and div is not None and div >= 0:
    signal = 'EXIT_READY'
elif pos > 0:
    signal = 'HOLDING'


def make_asset(ticker, name):
    return {
        'ticker': ticker, 'name': name,
        'dates': dates, 'closes': closes,
        'divergence': [s['divergence'] for s in states],
        'predicted': [s['predicted'] for s in states],
        'position': [s['position'] for s in states],
        'peak': [s['peak'] for s in states],
        'port_series': port, 'bh_series': bh,
        'trades': trades, 'latest_trades': trades[-10:],
        'metrics': {
            'total_return': round(port[-1] - 100, 2),
            'bh_return': round(bh[-1] - 100, 2),
            'mdd': round(mdd, 2),
            'trade_count': len([t for t in trades if t['type'].startswith('ENTRY')]),
        },
        'current': {
            'signal': signal, 'position': pos, 'divergence': div,
            'close': latest['close'], 'predicted': latest['predicted'],
            'peak': latest['peak'], 'date': dates[-1],
        },
        'params': {
            'reg_window_days': 252, 'entry1': -5.0, 'entry2': -10.0, 'entry3': -20.0,
            'exit': 0.0, 'stop_loss': -12.0, 'regression_method': 'peak_based',
        }
    }


output = {
    'generated_at': dt.datetime.utcnow().isoformat() + 'Z',
    'placeholder': True,
    'strategy': {
        'name': 'SGOV 3-Tier Rotation (Peak-Based Regression)',
        'portfolio': {'krw_pct': 80, 'usd_pct': 20, 'krw_annual_return': 3.5},
        'tiers': [
            {'level': 1, 'threshold': -5.0, 'dollar_weight': 35, 'portfolio_weight': 7},
            {'level': 2, 'threshold': -10.0, 'dollar_weight': 70, 'portfolio_weight': 14},
            {'level': 3, 'threshold': -20.0, 'dollar_weight': 100, 'portfolio_weight': 20},
        ],
        'exit_threshold': 0.0, 'stop_loss': -12.0,
        'regression_window_days': 252, 'regression_method': 'peak_based',
    },
    'assets': {
        'SPY': make_asset('SPY', 'S&P 500 (SPY) [placeholder]'),
        'QQQ': make_asset('QQQ', 'Nasdaq 100 (QQQ) [placeholder]'),
    }
}

out_dir = os.path.join(os.path.dirname(__file__), '..', 'docs')
out_path = os.path.join(out_dir, 'data.json')
with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

print(f"✅ placeholder data.json 생성 완료: {out_path}")
print(f"크기: {len(json.dumps(output)) / 1024:.1f} KB")
