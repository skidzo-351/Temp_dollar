#!/usr/bin/env python3
"""
최종 확정 전략 v4 — 일간 신호 계산 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
데이터:    일별 수집 → 월별 리샘플링 → 60M 회귀선 계산
           (백테스트와 동일한 기준, 현재가는 일별 최신값 표시)

포트폴리오: SPY 40% + QQQ 20% + SGOV 40%
신호:       SPY 60M 롤링 회귀선 괴리율

월 DCA (동적):
  괴리율 ≥ -3%  →  SPY $200 + QQQ $100 + SGOV $200
  괴리율 < -3%  →  SPY $250 + QQQ $125 + SGOV $125

진입:  1차(-5%) / 2차(-10%) / 3차(-15%)
리밸:  +15% 교차 → 전술+기본 통합 → 40:20:40 → SAFE
하락:  평단 -20%/-40% → 외부현금 25%+25%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
백테스트(1995-2024): $1,341,136 | CAGR 6.6% | MDD 50.2%
"""
import json, os, urllib.request, time
from datetime import datetime

# ── 전략 파라미터 ────────────────────────────────────────────
REG_WIN        = 60      # 60개월 (월별 기준, 백테스트와 동일)
W_SPY, W_QQQ, W_SGOV = 0.40, 0.20, 0.40

ENTRY1_DIV  = -5.0
ENTRY2_DIV  = -10.0
ENTRY3_DIV  = -15.0
ENTRY1_PCT  = 0.12
ENTRY2_PCT  = 0.12

REBAL_FULL_THR  = +15.0
DCA_DIV_THR     = -3.0
DCA_BASE = {'spy': 200, 'qqq': 100, 'sgov': 200}
DCA_BULL = {'spy': 250, 'qqq': 125, 'sgov': 125}

REBAL_DOWN_PCT   = 0.25
REBAL_DOWN1_LOSS = -20.0
REBAL_DOWN2_LOSS = -40.0

ASSETS = {
    'SPY': {'stooq': 'spy.us', 'yahoo': 'SPY', 'name': 'S&P 500 (SPY)'},
    'QQQ': {'stooq': 'qqq.us', 'yahoo': 'QQQ', 'name': 'Nasdaq 100 (QQQ)'},
}

# ── 일별 데이터 수집 ─────────────────────────────────────────
def fetch_stooq_daily(symbol, days=2000):
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/csv,*/*',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode('utf-8')
    except Exception as e:
        print(f"  ⚠ stooq ({symbol}): {e}"); return None
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

def fetch_yahoo_daily(symbol, days=2000):
    end = int(time.time()); start = end - days * 86400 * 2
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={start}&period2={end}&interval=1d")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode('utf-8'))
        res = data['chart']['result'][0]
        ts  = res['timestamp']
        cl  = res['indicators']['quote'][0]['close']
        recs = [{'date': datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'),
                 'close': round(c, 4)}
                for t, c in zip(ts, cl) if c is not None]
        return recs[-days:] if len(recs) > days else recs
    except Exception as e:
        print(f"  ⚠ yahoo ({symbol}): {e}"); return None

def fetch_daily(symbol_s, symbol_y, days=2000):
    print(f"  일별 수집: {symbol_s}…")
    d = fetch_stooq_daily(symbol_s, days)
    if d and len(d) > 100:
        print(f"    stooq {len(d)}개")
        return d
    print(f"  Yahoo fallback: {symbol_y}…")
    d = fetch_yahoo_daily(symbol_y, days)
    if d and len(d) > 100:
        print(f"    yahoo {len(d)}개")
        return d
    return []

# ── 일별 → 월별 리샘플링 ─────────────────────────────────────
def resample_monthly(daily_recs):
    """
    일별 데이터를 월별 종가로 리샘플링
    각 월의 마지막 거래일 종가 사용 (백테스트와 동일)
    """
    monthly = {}
    for r in daily_recs:
        ym = r['date'][:7]  # 'YYYY-MM'
        monthly[ym] = r     # 같은 달이면 덮어쓰기 → 마지막 거래일 남음

    result = []
    for ym in sorted(monthly.keys()):
        r = monthly[ym]
        result.append({'date': ym + '-01',  # 월 표시용 (실제론 월말 종가)
                       'date_label': ym,
                       'close': r['close'],
                       'date_actual': r['date']})  # 실제 거래일
    return result

# ── 선형회귀 ─────────────────────────────────────────────────
def linreg(vals):
    n = len(vals)
    if n < 2: return 0.0, vals[0] if vals else 0.0
    xm = (n-1)/2.0; ym = sum(vals)/n
    num = sum((i-xm)*(v-ym) for i,v in enumerate(vals))
    den = sum((i-xm)**2 for i in range(n))
    slope = num/den if den else 0.0
    return slope, ym-slope*xm

# ── 신호 계산 (월별 데이터 기준, 백테스트와 동일) ─────────────
def calc_signals(monthly_closes):
    N = len(monthly_closes)
    if N < REG_WIN + 5: return [], []

    es = 0; rs = 0; prev_over = False
    tac_cost = 0.0; tac_sh = 0.0
    trades = []; states = []
    running_peak = monthly_closes[0]
    pos_map = {0: 0.0, 1: 0.35, 2: 0.70, 3: 1.0}

    for i in range(REG_WIN, N):
        if es == 0 and monthly_closes[i] > running_peak:
            running_peak = monthly_closes[i]

        window = monthly_closes[i - REG_WIN: i]
        slope, intercept = linreg(window)
        predicted = intercept + slope * REG_WIN

        if not predicted:
            states.append({'i': i, 'close': monthly_closes[i],
                           'predicted': None, 'divergence': None,
                           'position': pos_map.get(es, 0.0), 'peak': running_peak})
            continue

        divergence = (monthly_closes[i] - predicted) / predicted * 100.0
        spy_avg  = tac_cost / tac_sh if tac_sh > 1e-9 else 0.0
        spy_loss = (monthly_closes[i] - spy_avg) / spy_avg * 100 if spy_avg > 0 else 0.0
        action = None

        # ① 완전 리밸: +15% 교차
        if divergence >= REBAL_FULL_THR and not prev_over:
            tac_cost = 0.0; tac_sh = 0.0
            es = 0; rs = 0; action = 'REBAL_FULL'
        prev_over = (divergence >= REBAL_FULL_THR)

        # ② 진입
        if action is None and es == 0 and divergence <= ENTRY1_DIV:
            tac_cost += monthly_closes[i] * ENTRY1_PCT
            tac_sh   += ENTRY1_PCT; es = 1; action = 'ENTRY1'
        elif action is None and es == 1 and divergence <= ENTRY2_DIV:
            tac_cost += monthly_closes[i] * ENTRY2_PCT
            tac_sh   += ENTRY2_PCT; es = 2; action = 'ENTRY2'
        elif action is None and es == 2 and divergence <= ENTRY3_DIV:
            amt = max(0.0, W_SGOV - ENTRY1_PCT - ENTRY2_PCT)
            tac_cost += monthly_closes[i] * amt
            tac_sh   += amt; es = 3; action = 'ENTRY3'

        # ③ 하락 리밸 (3차 이후)
        elif action is None and es == 3:
            if rs == 0 and spy_loss <= REBAL_DOWN1_LOSS:
                tac_cost += monthly_closes[i] * REBAL_DOWN_PCT
                tac_sh   += REBAL_DOWN_PCT; rs = 1; action = 'REBAL_DOWN1'
            elif rs == 1 and spy_loss <= REBAL_DOWN2_LOSS:
                tac_cost += monthly_closes[i] * REBAL_DOWN_PCT
                tac_sh   += REBAL_DOWN_PCT; rs = 2; action = 'REBAL_DOWN2'

        cur_pos = pos_map.get(es, 0.0)
        if action:
            trades.append({'index': i, 'type': action,
                           'divergence': round(divergence, 2),
                           'price': monthly_closes[i],
                           'position_after': cur_pos,
                           'avg_entry': round(spy_avg, 2) if spy_avg > 0 else None,
                           'spy_loss': round(spy_loss, 2)})
        states.append({'i': i, 'close': monthly_closes[i],
                       'predicted': round(predicted, 4),
                       'divergence': round(divergence, 4),
                       'position': cur_pos, 'peak': running_peak})

    return trades, states

# ── 현재 신호 판단 ────────────────────────────────────────────
def get_signal(states):
    if not states: return 'NO_SIGNAL'
    last = states[-1]; pos = last.get('position', 0.0); div = last.get('divergence')
    if div is None: return 'NO_SIGNAL'
    if pos > 0.0 and div >= REBAL_FULL_THR: return 'REBAL_FULL_READY'
    if pos == 0.0 and div <= ENTRY1_DIV:    return 'ENTRY1_READY'
    if pos == 0.35 and div <= ENTRY2_DIV:   return 'ENTRY2_READY'
    if pos == 0.70 and div <= ENTRY3_DIV:   return 'ENTRY3_READY'
    if pos > 0.0:                            return 'HOLDING'
    return 'WAITING'

SIG_MSG = {
    'WAITING':          '관망 중 — SGOV 보유',
    'ENTRY1_READY':     '⚡ 1차 진입 — SGOV 순자산×12% → SPY:QQQ=2:1',
    'ENTRY2_READY':     '⚡ 2차 진입 — SGOV 순자산×12% → SPY:QQQ=2:1',
    'ENTRY3_READY':     '⚡ 3차 진입 — SGOV 잔액 전부 → SPY:QQQ=2:1',
    'HOLDING':          '전술 포지션 보유 중',
    'REBAL_FULL_READY': '⚖️ 완전 리밸 — 전술+기본 통합 → 40:20:40 → SAFE',
    'NO_SIGNAL':        '신호 없음 (데이터 부족)',
}

# ── 자산별 빌드 ───────────────────────────────────────────────
def build(ticker, cfg):
    print(f"\n[{ticker}]")

    # 1) 일별 데이터 수집 (최소 60개월+여유분 = 약 1500거래일 필요)
    daily = fetch_daily(cfg['stooq'], cfg['yahoo'], days=2000)
    if not daily:
        print(f"  ✗ 데이터 수집 실패")
        return None

    # 2) 월별 리샘플링 (백테스트와 동일한 기준)
    monthly = resample_monthly(daily)
    if len(monthly) < REG_WIN + 10:
        print(f"  ✗ 월별 데이터 부족 ({len(monthly)}개월, 필요 {REG_WIN+10}개월)")
        return None
    print(f"  리샘플링: 일별 {len(daily)}개 → 월별 {len(monthly)}개월")

    m_dates  = [r['date_label'] for r in monthly]   # 'YYYY-MM'
    m_dates_actual = [r['date_actual'] for r in monthly]  # 실제 월말 거래일
    m_closes = [r['close'] for r in monthly]

    # 3) 신호 계산 (월별 기준, 백테스트와 완전 동일)
    trades, states = calc_signals(m_closes)
    sig = get_signal(states)

    # 4) 현재 정보
    last = states[-1] if states else {}
    pos  = last.get('position', 0.0)
    div  = last.get('divergence')
    pred = last.get('predicted')
    peak = last.get('peak')

    # 현재가: 일별 최신값 (괴리율 계산은 월별이지만 현재가는 일별)
    latest_close = daily[-1]['close']
    latest_date  = daily[-1]['date']

    # 현재가 기준 실시간 괴리율 (참고용, 신호는 월말 기준)
    div_realtime = round((latest_close - pred) / pred * 100, 2) if pred else None

    # 시리즈 패딩 (월별 기준, 차트용)
    pad    = REG_WIN
    s_div  = [None]       * pad + [s['divergence'] for s in states]
    s_pred = [None]       * pad + [s['predicted']  for s in states]
    s_pos  = [0.0]        * pad + [s['position']   for s in states]
    s_peak = [m_closes[0]] * pad + [s['peak']      for s in states]

    # 일별 데이터도 함께 저장 (차트 확대용)
    daily_dates  = [r['date']  for r in daily]
    daily_closes = [r['close'] for r in daily]

    n_entry = sum(1 for t in trades if t['type'].startswith('ENTRY'))
    n_rebal = sum(1 for t in trades if 'REBAL' in t['type'])

    print(f"  ✓ 신호={sig} | 월말괴리율={div}% | 실시간괴리율={div_realtime}%")
    print(f"    월말종가={m_closes[-1]} ({m_dates_actual[-1]}) | 현재가={latest_close} ({latest_date})")
    print(f"    예측가={pred} | 진입{n_entry}회 | 리밸{n_rebal}회")

    return {
        'ticker': ticker, 'name': cfg['name'],

        # 월별 시리즈 (회귀선/괴리율 차트, 백테스트 기준)
        'dates':      m_dates,
        'dates_actual': m_dates_actual,
        'closes':     m_closes,
        'divergence': s_div,
        'predicted':  s_pred,
        'position':   s_pos,
        'peak':       s_peak,

        # 일별 시리즈 (현재가 차트, 최근 구간)
        'daily_dates':  daily_dates,
        'daily_closes': daily_closes,

        # 거래 기록 (월별 index 기준)
        'trades':        trades,
        'latest_trades': trades[-15:],

        # 현재 상태
        'current': {
            'signal':       sig,
            'msg':          SIG_MSG.get(sig, sig),
            'position':     pos,
            # 신호 기준 (월말 종가 기반, 백테스트 동일)
            'divergence':   div,           # 월말 기준 괴리율 (신호 판단)
            'div_realtime': div_realtime,  # 현재가 기준 실시간 괴리율 (참고용)
            'close':        latest_close,  # 현재가 (일별 최신)
            'close_monthly': m_closes[-1], # 월말 종가
            'predicted':    pred,          # 회귀선 예측가
            'peak':         peak,
            'date':         latest_date,   # 현재가 기준일
            'date_monthly': m_dates_actual[-1],  # 월말 기준일
        },

        'params': {
            'portfolio':     'SPY40+QQQ20+SGOV40',
            'signal':        'SPY 60M 롤링 회귀선 괴리율 (월별 리샘플)',
            'data_freq':     'daily→monthly resample',
            'reg_win':       REG_WIN,
            'entry_div':     [ENTRY1_DIV, ENTRY2_DIV, ENTRY3_DIV],
            'entry_pct':     [ENTRY1_PCT*100, ENTRY2_PCT*100, '잔액전부(≈16%)'],
            'rebal_full':    REBAL_FULL_THR,
            'rebal_down':    [REBAL_DOWN1_LOSS, REBAL_DOWN2_LOSS, REBAL_DOWN_PCT*100],
            'dca_base':      DCA_BASE,
            'dca_bull':      DCA_BULL,
            'dca_div_thr':   DCA_DIV_THR,
        },
    }

# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("최종 전략 v4 — 일별수집→월별리샘플 | 60M 회귀선")
    print(f"실행: {datetime.utcnow().isoformat()} UTC")
    print(f"REG={REG_WIN}개월 | 리밸=+{REBAL_FULL_THR}% | DCA동적")
    print("백테스트와 동일한 월별 기준 신호 계산")
    print("=" * 65)

    output = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name':    'Final v4 — SPY40+QQQ20+SGOV40 | 60M Monthly Resample',
            'version': 'v4',
            'note':    '일별 수집 → 월별 리샘플 → 60M 회귀 (백테스트 동일 기준)',
            'portfolio': {'spy': W_SPY, 'qqq': W_QQQ, 'sgov': W_SGOV},
            'dca': {'base': DCA_BASE, 'bull': DCA_BULL, 'div_thr': DCA_DIV_THR},
            'entry': [
                {'level':1,'div':ENTRY1_DIV,'pct':f'{ENTRY1_PCT*100:.0f}%'},
                {'level':2,'div':ENTRY2_DIV,'pct':f'{ENTRY2_PCT*100:.0f}%'},
                {'level':3,'div':ENTRY3_DIV,'pct':'SGOV잔액전부(≈16%)'},
            ],
            'rebal_full': {'trigger':f'+{REBAL_FULL_THR}% 교차',
                           'action':'전술+기본통합→40:20:40→SAFE'},
            'rebal_down': {'t1':REBAL_DOWN1_LOSS,'t2':REBAL_DOWN2_LOSS,
                           'pct':f'{REBAL_DOWN_PCT*100:.0f}%'},
            'backtest': {'final':1341136,'cagr':6.6,'mdd':50.2,'trades':26},
        },
        'assets': {},
    }

    out_path = 'docs/data.json'
    for ticker, cfg in ASSETS.items():
        try:
            result = build(ticker, cfg)
        except Exception as e:
            print(f"  ✗ {ticker}: {e}"); result = None
        if result:
            output['assets'][ticker] = result
        time.sleep(1)

    if not output['assets']:
        print("\n❌ 수집 실패 — 기존 data.json 유지")
        if os.path.exists(out_path): return True
        output['error'] = 'fetch_failed'

    if os.path.exists(out_path) and len(output['assets']) < len(ASSETS):
        try:
            with open(out_path) as f: prev = json.load(f)
            for t in ASSETS:
                if t not in output['assets'] and t in prev.get('assets', {}):
                    output['assets'][t] = prev['assets'][t]
                    print(f"  ↩ {t}: 이전 데이터 유지")
        except Exception as e:
            print(f"  ⚠ 병합 실패: {e}")

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    print(f"\n✅ {out_path} ({os.path.getsize(out_path)//1024}KB)")
    return True

if __name__ == '__main__':
    exit(0 if main() else 1)
