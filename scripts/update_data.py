#!/usr/bin/env python3
"""
SGOV 3단계 로테이션 전략 - 일간 신호 계산 스크립트
신고가 기반 회귀선 + 청산 0% 방식  (수정: 롤링 신고가 + 최근 REG_WIN 구간)
"""
import json, os, urllib.request, time, math
from datetime import datetime

# ─── 전략 파라미터 ───────────────────────────────────────────
REG_WIN    = 200   # 12개월 거래일 기준
ENTRY1     = -5.0
ENTRY2     = -10.0
ENTRY3     = -20.0
EXIT_THR   = 5.0   # 회귀선 괴리율 0% = 복귀 시점
STOP_LOSS  = -12.0 # 진입가 대비 손절

ASSETS = {
    'SPY': {'stooq':'spy.us','yahoo':'SPY','name':'S&P 500 (SPY)'},
    'QQQ': {'stooq':'qqq.us','yahoo':'QQQ','name':'Nasdaq 100 (QQQ)'},
}

# ─── 데이터 수집 ─────────────────────────────────────────────
def _stooq(symbol, domain='com', days=900):
    url = f"https://stooq.{domain}/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 Chrome/120.0 Safari/537.36'),
        'Accept': 'text/csv,*/*',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode('utf-8')
    except Exception as e:
        print(f"  ⚠ stooq.{domain} ({symbol}): {e}")
        return None
    lines = text.strip().split('\n')
    if len(lines) < 2: return None
    hdr = lines[0].split(',')
    try: di, ci = hdr.index('Date'), hdr.index('Close')
    except ValueError: return None
    recs = []
    for ln in lines[1:]:
        p = ln.split(',')
        try: recs.append({'date': p[di], 'close': float(p[ci])})
        except: pass
    return recs[-days:] if len(recs) > days else recs

def _yahoo(symbol, days=900):
    end = int(time.time())
    start = end - days * 86400 * 2
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={start}&period2={end}&interval=1d")
    req = urllib.request.Request(url, headers={
        'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 Chrome/120.0 Safari/537.36'),
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode('utf-8'))
        res = data['chart']['result'][0]
        ts  = res['timestamp']
        cl  = res['indicators']['quote'][0]['close']
        recs = [{'date': datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'), 'close': round(c, 4)}
                for t, c in zip(ts, cl) if c is not None]
        return recs[-days:] if len(recs) > days else recs
    except Exception as e:
        print(f"  ⚠ yahoo ({symbol}): {e}")
        return None

def fetch(symbol_s, symbol_y, days=900):
    for src in [lambda: _stooq(symbol_s,'com',days),
                lambda: _stooq(symbol_s,'pl',days),
                lambda: _yahoo(symbol_y, days)]:
        d = src()
        if d and len(d) > 50:
            return d
    return []

# ─── 선형회귀 ────────────────────────────────────────────────
def linreg(vals):
    n = len(vals)
    if n < 2: return 0.0, vals[0] if vals else 0.0
    xm = (n - 1) / 2.0
    ym = sum(vals) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    den = sum((i - xm) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    return slope, ym - slope * xm

# ─── 신호 계산 (핵심 수정) ───────────────────────────────────
def calc_signals(closes):
    """
    롤링 12M 회귀선 기반 3단계 진입/청산
    - 매 시점 최근 REG_WIN 구간으로 회귀선 계산 (t_offset 폭발 방지)
    - 신고가(peak)는 SAFE 상태에서만 갱신 (표시용)
    - 손절 기준: 각 단계별 가중평균 진입가 기준 -12%
    """
    N = len(closes)
    if N < REG_WIN + 5:
        return [], []

    dollar_pos   = 0.0
    entry_price  = None   # 가중평균 진입가
    entry_amount = 0.0    # 누적 매수 금액 비중 (포지션 비중 × 가격)
    trades       = []
    states       = []
    running_peak = closes[0]

    for i in range(REG_WIN, N):
        # SAFE 상태에서만 신고가 갱신
        if dollar_pos == 0.0 and closes[i] > running_peak:
            running_peak = closes[i]

        # 롤링 회귀선: 과거 REG_WIN 봉 → 다음 봉 예측
        window = closes[i - REG_WIN: i]
        slope, intercept = linreg(window)
        predicted = intercept + slope * REG_WIN

        if predicted == 0:
            states.append({'i': i, 'close': closes[i], 'predicted': None,
                           'divergence': None, 'position': dollar_pos,
                           'peak': running_peak})
            continue

        divergence = (closes[i] - predicted) / predicted * 100.0
        action = None

        # 손절: 가중평균 진입가 대비 -12%
        if dollar_pos > 0 and entry_price is not None:
            if (closes[i] - entry_price) / entry_price * 100.0 <= STOP_LOSS:
                action = 'STOP_LOSS'
                dollar_pos   = 0.0
                entry_price  = None
                entry_amount = 0.0

        if action is None:
            if dollar_pos == 0.0:
                if divergence <= ENTRY1:
                    # 1차: 달러자산의 35%
                    new_pos      = 0.35
                    entry_amount = 0.35 * closes[i]
                    dollar_pos   = new_pos
                    entry_price  = closes[i]   # 단순: 1차 진입가
                    action = 'ENTRY1'

            elif dollar_pos == 0.35:
                if divergence <= ENTRY2:
                    # 2차: 달러자산의 70% (추가 35%)
                    # 가중평균 진입가 갱신
                    prev_amount  = entry_amount                     # 0.35 × 1차가격
                    add_amount   = 0.35 * closes[i]                 # 0.35 × 2차가격
                    entry_amount = prev_amount + add_amount
                    entry_price  = entry_amount / 0.70              # 가중평균
                    dollar_pos   = 0.70
                    action = 'ENTRY2'
                elif divergence >= EXIT_THR:
                    dollar_pos = 0.0; entry_price = None; entry_amount = 0.0; action = 'EXIT'

            elif dollar_pos == 0.70:
                if divergence <= ENTRY3:
                    # 3차: 달러자산의 100% (추가 30%)
                    prev_amount  = entry_amount                     # 0.70 × 평균가
                    add_amount   = 0.30 * closes[i]                 # 0.30 × 3차가격
                    entry_amount = prev_amount + add_amount
                    entry_price  = entry_amount / 1.00              # 가중평균
                    dollar_pos   = 1.0
                    action = 'ENTRY3'
                elif divergence >= EXIT_THR:
                    dollar_pos = 0.0; entry_price = None; entry_amount = 0.0; action = 'EXIT'

            elif dollar_pos == 1.0:
                if divergence >= EXIT_THR:
                    dollar_pos = 0.0; entry_price = None; entry_amount = 0.0; action = 'EXIT'

        if action:
            trades.append({'index': i, 'type': action,
                           'divergence': round(divergence, 2),
                           'price': closes[i],
                           'position_after': dollar_pos,
                           'avg_entry': round(entry_price, 2) if entry_price else None})

        states.append({'i': i, 'close': closes[i],
                       'predicted': round(predicted, 4),
                       'divergence': round(divergence, 4),
                       'position': dollar_pos,
                       'peak': running_peak})

    return trades, states

# ─── 포트폴리오 시뮬 ─────────────────────────────────────────
def backtest(states, krw_daily=0.035/252):
    port = [100.0]
    for k in range(1, len(states)):
        prev = states[k-1]
        cur  = states[k]
        dr   = (cur['close'] / prev['close'] - 1) if prev['close'] else 0
        port.append(port[-1] * (1 + 0.20 * prev['position'] * dr + 0.80 * krw_daily))
    return port

def mdd(series):
    peak = series[0]; worst = 0.0
    for v in series:
        if v > peak: peak = v
        worst = max(worst, (peak - v) / peak * 100 if peak else 0)
    return worst

# ─── 자산별 페이로드 빌드 ────────────────────────────────────
def build(ticker, cfg):
    print(f"\n[{ticker}] 데이터 수집 중…")
    recs = fetch(cfg['stooq'], cfg['yahoo'])
    if not recs or len(recs) < REG_WIN + 20:
        print(f"  ✗ 데이터 부족")
        return None

    dates  = [r['date']  for r in recs]
    closes = [r['close'] for r in recs]

    trades, states = calc_signals(closes)
    port    = backtest(states)
    bh      = [100.0]
    kd      = 0.035 / 252
    for _ in range(1, len(states)):
        bh.append(bh[-1] * (1 + 0.80 * kd))

    # 현재 신호
    cur = states[-1] if states else {}
    pos = cur.get('position', 0.0)
    div = cur.get('divergence')
    sig = 'WAITING'
    if   pos == 0.0   and div is not None and div <= ENTRY1:  sig = 'ENTRY1_READY'
    elif pos == 0.35  and div is not None and div <= ENTRY2:  sig = 'ENTRY2_READY'
    elif pos == 0.70  and div is not None and div <= ENTRY3:  sig = 'ENTRY3_READY'
    elif pos >  0.0   and div is not None and div >= EXIT_THR: sig = 'EXIT_READY'
    elif pos >  0.0:  sig = 'HOLDING'

    # 날짜 인덱스: states[k].i → recs의 실제 인덱스
    def date_of(state_i):
        return dates[state_i] if state_i < len(dates) else '—'

    # dates/closes/predicted/divergence/position 시리즈 정렬
    # states는 closes[REG_WIN:]에 대응하므로 앞에 None 패딩
    pad    = REG_WIN
    s_div  = [None]*pad + [s['divergence'] for s in states]
    s_pred = [None]*pad + [s['predicted']  for s in states]
    s_pos  = [0.0]*pad  + [s['position']   for s in states]
    s_peak = [closes[0]]*pad + [s['peak']  for s in states]

    # port/bh도 동일 길이로 패딩
    s_port = [100.0]*pad + port
    s_bh   = [100.0]*pad + bh

    n_entries = sum(1 for t in trades if t['type'].startswith('ENTRY'))
    metrics = {
        'total_return': round(port[-1] - 100, 2) if port else 0,
        'bh_return':    round(bh[-1]   - 100, 2) if bh   else 0,
        'mdd':          round(mdd(port), 2),
        'trade_count':  n_entries,
    }

    cur_peak = states[-1]['peak'] if states else closes[-1]
    print(f"  ✓ 수익 {metrics['total_return']}% | 신호={sig} | 포지션={pos*20:.0f}% | 거래={n_entries}회")
    print(f"    종가={cur.get('close','?')} | 예측가={cur.get('predicted','?')} | 괴리율={div}% | 신고가={cur_peak:.2f}")

    return {
        'ticker': ticker, 'name': cfg['name'],
        'dates': dates, 'closes': closes,
        'divergence': s_div, 'predicted': s_pred,
        'position': s_pos, 'peak': s_peak,
        'port_series': s_port, 'bh_series': s_bh,
        'trades': trades,
        'latest_trades': trades[-10:],
        'metrics': metrics,
        'current': {
            'signal': sig, 'position': pos,
            'divergence': div,
            'close': cur.get('close'),
            'predicted': cur.get('predicted'),
            'peak': cur_peak,
            'date': dates[-1] if dates else None,
        },
        'params': {
            'reg_window_days': REG_WIN,
            'entry1': ENTRY1, 'entry2': ENTRY2, 'entry3': ENTRY3,
            'exit': EXIT_THR, 'stop_loss': STOP_LOSS,
            'regression_method': 'rolling_12m',
        }
    }

# ─── 메인 ────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("SGOV 3단계 로테이션 — 일간 신호 갱신")
    print(f"실행: {datetime.utcnow().isoformat()} UTC")
    print("=" * 60)

    out = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name': 'SGOV 3-Tier Rotation (Rolling 12M Regression)',
            'portfolio': {'krw_pct': 80, 'usd_pct': 20, 'krw_annual_return': 3.5},
            'tiers': [
                {'level':1,'threshold':ENTRY1,'dollar_weight':35,'portfolio_weight':7},
                {'level':2,'threshold':ENTRY2,'dollar_weight':70,'portfolio_weight':14},
                {'level':3,'threshold':ENTRY3,'dollar_weight':100,'portfolio_weight':20},
            ],
            'exit_threshold': EXIT_THR, 'stop_loss': STOP_LOSS,
            'regression_window_days': REG_WIN,
        },
        'assets': {}
    }

    for ticker, cfg in ASSETS.items():
        try:
            result = build(ticker, cfg)
        except Exception as e:
            print(f"  ✗ {ticker} 예외: {e}")
            result = None
        if result:
            out['assets'][ticker] = result
        time.sleep(1)

    out_path = 'docs/data.json'

    if not out['assets']:
        print("\n❌ 수집 실패. 기존 data.json 유지.")
        if os.path.exists(out_path):
            return True
        out['error'] = 'fetch_failed'

    # 누락 자산 보강
    if os.path.exists(out_path) and len(out['assets']) < len(ASSETS):
        try:
            with open(out_path) as f:
                prev = json.load(f)
            for t in ASSETS:
                if t not in out['assets'] and t in prev.get('assets', {}):
                    out['assets'][t] = prev['assets'][t]
                    print(f"  ↩ {t}: 이전 데이터 유지")
        except Exception as e:
            print(f"  ⚠ 병합 실패: {e}")

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    size = os.path.getsize(out_path)
    print(f"\n✅ 저장 완료: {out_path} ({size//1024}KB)")
    return True

if __name__ == '__main__':
    exit(0 if main() else 1)
