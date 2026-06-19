#!/usr/bin/env python3
"""
SGOV 3단계 로테이션 전략 - 일간 신호 계산 스크립트
신고가 기반 회귀선 + 청산 0% 방식

매일 GitHub Actions에 의해 실행되어 data.json을 갱신합니다.
"""

import json
import os
import urllib.request
import time
from datetime import datetime, timedelta
import math


# ─────────────────────────────────────────────────────────────
# 1. 데이터 수집 (Stooq - 무료, API key 불필요)
# ─────────────────────────────────────────────────────────────

def fetch_stooq_daily(symbol, days=900):
    """
    Stooq에서 일간 종가 데이터 수집
    symbol: 'spy.us', 'qqq.us' 등
    """
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        'Accept': 'text/csv,*/*',
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            text = response.read().decode('utf-8')
    except Exception as e:
        print(f"  ⚠️  Stooq 요청 실패 ({symbol}): {e}")
        return None

    lines = text.strip().split('\n')
    if len(lines) < 2:
        return None

    header = lines[0].split(',')
    try:
        date_idx = header.index('Date')
        close_idx = header.index('Close')
    except ValueError:
        return None

    records = []
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) <= max(date_idx, close_idx):
            continue
        try:
            d = parts[date_idx]
            c = float(parts[close_idx])
            records.append({'date': d, 'close': c})
        except (ValueError, IndexError):
            continue

    # 최근 N일만 사용
    return records[-days:] if len(records) > days else records


def fetch_yahoo_fallback(symbol, days=900):
    """
    Stooq 실패시 Yahoo Finance 폴백
    """
    end = int(time.time())
    start = end - days * 86400 * 2  # 여유있게 가져옴 (휴장일 고려)
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={start}&period2={end}&interval=1d")
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        'Accept': 'application/json',
    }
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"  ⚠️  Yahoo 요청 실패 ({symbol}): {e}")
        return None

    try:
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        closes = result['indicators']['quote'][0]['close']
    except (KeyError, IndexError, TypeError):
        return None

    records = []
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        d = datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
        records.append({'date': d, 'close': round(c, 4)})

    return records[-days:] if len(records) > days else records


def fetch_stooq_alt(symbol, days=900):
    """
    Stooq 대체 엔드포인트 (다른 도메인 패턴)
    """
    url = f"https://stooq.pl/q/d/l/?s={symbol}&i=d"
    headers = {
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/120.0.0.0 Safari/537.36'),
        'Accept': 'text/csv,*/*',
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            text = response.read().decode('utf-8')
    except Exception as e:
        print(f"  ⚠️  Stooq(alt) 요청 실패 ({symbol}): {e}")
        return None

    lines = text.strip().split('\n')
    if len(lines) < 2:
        return None
    header = lines[0].split(',')
    try:
        date_idx = header.index('Date')
        close_idx = header.index('Close')
    except ValueError:
        return None

    records = []
    for line in lines[1:]:
        parts = line.split(',')
        if len(parts) <= max(date_idx, close_idx):
            continue
        try:
            records.append({'date': parts[date_idx], 'close': float(parts[close_idx])})
        except (ValueError, IndexError):
            continue
    return records[-days:] if len(records) > days else records


def get_price_history(symbol_stooq, symbol_yahoo, days=900):
    """Stooq(.com) -> Stooq(.pl) -> Yahoo 순으로 폴백"""
    print(f"  데이터 수집 중: {symbol_stooq} ...")
    data = fetch_stooq_daily(symbol_stooq, days)
    if data and len(data) > 50:
        print(f"    ✓ Stooq(.com)에서 {len(data)}개 레코드 수집")
        return data

    print(f"  Stooq(.com) 실패, Stooq(.pl)로 재시도 ...")
    data = fetch_stooq_alt(symbol_stooq, days)
    if data and len(data) > 50:
        print(f"    ✓ Stooq(.pl)에서 {len(data)}개 레코드 수집")
        return data

    print(f"  Stooq 전체 실패, Yahoo로 재시도: {symbol_yahoo} ...")
    data = fetch_yahoo_fallback(symbol_yahoo, days)
    if data and len(data) > 50:
        print(f"    ✓ Yahoo에서 {len(data)}개 레코드 수집")
        return data

    print(f"    ✗ 데이터 수집 실패: {symbol_stooq}/{symbol_yahoo}")
    return []


# ─────────────────────────────────────────────────────────────
# 2. 선형회귀 + 신고가 기반 신호 계산
# ─────────────────────────────────────────────────────────────

def linreg(values):
    """단순 선형회귀: y = a + b*x, x = 0..n-1"""
    n = len(values)
    if n < 2:
        return 0.0, values[0] if values else 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0, y_mean
    slope = num / den
    intercept = y_mean - slope * x_mean
    return slope, intercept


REG_WIN_DAYS = 252  # 12개월(거래일 기준)
ENTRY1, ENTRY2, ENTRY3 = -5.0, -10.0, -20.0
EXIT_THR = 0.0  # 신고가 기반 + 청산 0%
STOP_LOSS_PCT = -12.0  # 진입가 대비 손절


def calculate_signal_series(closes):
    """
    신고가 기반 회귀선 + 3단계 진입/청산 시뮬레이션
    closes: 일간 종가 리스트 (오래된 -> 최신 순)

    return: 전체 거래기록, 각 일자별 상태(dollar_pos, divergence, predicted, peak)
    """
    n = len(closes)
    if n < 30:
        return [], []

    dollar_pos = 0.0
    running_peak = closes[0]
    peak_idx = 0
    entry_price = None

    trades = []
    daily_states = []

    for i in range(1, n):
        # SAFE 상태일 때만 신고가 갱신
        if dollar_pos == 0.0 and closes[i] > running_peak:
            running_peak = closes[i]
            peak_idx = i

        reg_start = max(0, peak_idx - REG_WIN_DAYS)
        reg_end = peak_idx + 1
        window = closes[reg_start:reg_end]

        if len(window) < 2:
            daily_states.append({
                'i': i, 'close': closes[i], 'predicted': None,
                'divergence': None, 'position': dollar_pos,
                'peak': running_peak, 'peak_idx': peak_idx
            })
            continue

        slope, intercept = linreg(window)
        t_offset = i - reg_start
        predicted = intercept + slope * t_offset

        if predicted == 0:
            daily_states.append({
                'i': i, 'close': closes[i], 'predicted': None,
                'divergence': None, 'position': dollar_pos,
                'peak': running_peak, 'peak_idx': peak_idx
            })
            continue

        divergence = (closes[i] - predicted) / predicted * 100.0

        action = None
        # 손절 체크 (최우선)
        if dollar_pos > 0 and entry_price is not None:
            dd_from_entry = (closes[i] - entry_price) / entry_price * 100.0
            if dd_from_entry <= STOP_LOSS_PCT:
                action = 'STOP_LOSS'
                dollar_pos = 0.0
                entry_price = None
                running_peak = closes[i]
                peak_idx = i

        if action is None:
            if dollar_pos == 0.0:
                if divergence <= ENTRY1:
                    dollar_pos = 0.35
                    entry_price = closes[i]
                    action = 'ENTRY1'
            elif dollar_pos == 0.35:
                if divergence <= ENTRY2:
                    dollar_pos = 0.70
                    action = 'ENTRY2'
                elif divergence >= EXIT_THR:
                    dollar_pos = 0.0
                    entry_price = None
                    running_peak = closes[i]
                    peak_idx = i
                    action = 'EXIT'
            elif dollar_pos == 0.70:
                if divergence <= ENTRY3:
                    dollar_pos = 1.0
                    action = 'ENTRY3'
                elif divergence >= EXIT_THR:
                    dollar_pos = 0.0
                    entry_price = None
                    running_peak = closes[i]
                    peak_idx = i
                    action = 'EXIT'
            elif dollar_pos == 1.0:
                if divergence >= EXIT_THR:
                    dollar_pos = 0.0
                    entry_price = None
                    running_peak = closes[i]
                    peak_idx = i
                    action = 'EXIT'

        if action:
            trades.append({
                'index': i, 'type': action, 'divergence': round(divergence, 2),
                'price': closes[i], 'position_after': dollar_pos
            })

        daily_states.append({
            'i': i, 'close': closes[i], 'predicted': round(predicted, 4),
            'divergence': round(divergence, 4), 'position': dollar_pos,
            'peak': running_peak, 'peak_idx': peak_idx
        })

    return trades, daily_states


def backtest_portfolio(daily_states, dates, krw_annual_return=0.035):
    """전체 포트폴리오(원화80%+달러20%) 가치 추이 계산"""
    krw_monthly = krw_annual_return / 252  # 일간 근사
    port = 100.0
    series = [100.0]

    for idx in range(1, len(daily_states)):
        prev_pos = daily_states[idx - 1]['position']
        prev_close = daily_states[idx - 1]['close']
        curr_close = daily_states[idx]['close']
        dollar_ret = (curr_close / prev_close - 1) if prev_close else 0
        port *= (1 + 0.20 * (prev_pos * dollar_ret) + 0.80 * krw_monthly)
        series.append(port)

    return series


def compute_max_drawdown(series):
    peak = series[0] if series else 100.0
    mdd = 0.0
    for v in series:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
    return mdd


# ─────────────────────────────────────────────────────────────
# 3. 메인 실행
# ─────────────────────────────────────────────────────────────

ASSETS = {
    'SPY': {'stooq': 'spy.us', 'yahoo': 'SPY', 'name': 'S&P 500 (SPY)'},
    'QQQ': {'stooq': 'qqq.us', 'yahoo': 'QQQ', 'name': 'Nasdaq 100 (QQQ)'},
}


def build_asset_payload(ticker, cfg):
    print(f"\n[{ticker}] 처리 시작")
    records = get_price_history(cfg['stooq'], cfg['yahoo'], days=900)

    if not records or len(records) < 60:
        print(f"  ✗ {ticker} 데이터 부족, 스킵")
        return None

    dates = [r['date'] for r in records]
    closes = [r['close'] for r in records]

    trades, daily_states = calculate_signal_series(closes)
    port_series = backtest_portfolio(daily_states, dates)
    mdd = compute_max_drawdown(port_series)

    bh_series = [100.0]
    krw_daily = 0.035 / 252
    for _ in range(1, len(daily_states)):
        bh_series.append(bh_series[-1] * (1 + 0.80 * krw_daily))

    latest_state = daily_states[-1] if daily_states else None
    latest_trades = trades[-10:] if trades else []

    # 현재 신호 판단 (다음 행동 가이드)
    current_signal = 'HOLD'
    current_position = 0.0
    if latest_state:
        current_position = latest_state['position']
        div = latest_state['divergence']
        if div is not None:
            if current_position == 0.0 and div <= ENTRY1:
                current_signal = 'ENTRY1_READY'
            elif current_position == 0.35 and div <= ENTRY2:
                current_signal = 'ENTRY2_READY'
            elif current_position == 0.70 and div <= ENTRY3:
                current_signal = 'ENTRY3_READY'
            elif current_position > 0 and div >= EXIT_THR:
                current_signal = 'EXIT_READY'
            elif current_position > 0:
                current_signal = 'HOLDING'
            else:
                current_signal = 'WAITING'

    payload = {
        'ticker': ticker,
        'name': cfg['name'],
        'dates': dates,
        'closes': closes,
        'divergence': [s['divergence'] for s in daily_states],
        'predicted': [s['predicted'] for s in daily_states],
        'position': [s['position'] for s in daily_states],
        'peak': [s['peak'] for s in daily_states],
        'port_series': port_series,
        'bh_series': bh_series,
        'trades': trades,
        'latest_trades': latest_trades,
        'metrics': {
            'total_return': round(port_series[-1] - 100, 2) if port_series else 0,
            'bh_return': round(bh_series[-1] - 100, 2) if bh_series else 0,
            'mdd': round(mdd, 2),
            'trade_count': len([t for t in trades if t['type'].startswith('ENTRY')]),
        },
        'current': {
            'signal': current_signal,
            'position': current_position,
            'divergence': latest_state['divergence'] if latest_state else None,
            'close': latest_state['close'] if latest_state else None,
            'predicted': latest_state['predicted'] if latest_state else None,
            'peak': latest_state['peak'] if latest_state else None,
            'date': dates[-1] if dates else None,
        },
        'params': {
            'reg_window_days': REG_WIN_DAYS,
            'entry1': ENTRY1, 'entry2': ENTRY2, 'entry3': ENTRY3,
            'exit': EXIT_THR, 'stop_loss': STOP_LOSS_PCT,
            'regression_method': 'peak_based',
        }
    }

    print(f"  ✓ {ticker} 완료: 수익률 {payload['metrics']['total_return']}%, "
          f"신호={current_signal}, 포지션={current_position*20:.0f}%")

    return payload


def main():
    print("=" * 60)
    print("SGOV 3단계 로테이션 전략 - 일간 데이터 갱신")
    print(f"실행 시각: {datetime.utcnow().isoformat()} UTC")
    print("=" * 60)

    output = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name': 'SGOV 3-Tier Rotation (Peak-Based Regression)',
            'portfolio': {'krw_pct': 80, 'usd_pct': 20, 'krw_annual_return': 3.5},
            'tiers': [
                {'level': 1, 'threshold': ENTRY1, 'dollar_weight': 35, 'portfolio_weight': 7},
                {'level': 2, 'threshold': ENTRY2, 'dollar_weight': 70, 'portfolio_weight': 14},
                {'level': 3, 'threshold': ENTRY3, 'dollar_weight': 100, 'portfolio_weight': 20},
            ],
            'exit_threshold': EXIT_THR,
            'stop_loss': STOP_LOSS_PCT,
            'regression_window_days': REG_WIN_DAYS,
            'regression_method': 'peak_based',
        },
        'assets': {}
    }

    for ticker, cfg in ASSETS.items():
        try:
            result = build_asset_payload(ticker, cfg)
        except Exception as e:
            print(f"  ✗ {ticker} 처리 중 예외 발생: {e}")
            result = None
        if result:
            output['assets'][ticker] = result
        time.sleep(1)  # rate limit 보호

    out_path = 'docs/data.json'

    if not output['assets']:
        print("\n❌ 모든 자산 데이터 수집 실패.")
        if os.path.exists(out_path):
            print("   기존 docs/data.json을 유지합니다 (변경 없음).")
            return True  # 워크플로우 자체는 실패로 처리하지 않음
        else:
            print("   기존 파일도 없어 placeholder를 생성합니다.")
            output['error'] = 'initial_fetch_failed'

    # 일부만 성공한 경우, 기존 파일에서 누락 자산 보강
    if os.path.exists(out_path) and len(output['assets']) < len(ASSETS):
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                prev = json.load(f)
            for ticker in ASSETS:
                if ticker not in output['assets'] and ticker in prev.get('assets', {}):
                    print(f"  ↩️  {ticker}는 이전 데이터로 유지")
                    output['assets'][ticker] = prev['assets'][ticker]
        except Exception as e:
            print(f"  ⚠️ 이전 데이터 병합 실패: {e}")

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))

    print(f"\n✅ 데이터 저장 완료: {out_path}")
    print(f"   파일 크기: {len(json.dumps(output))/1024:.1f} KB")
    return True


if __name__ == '__main__':
    success = main()
    exit(0 if success else 1)
