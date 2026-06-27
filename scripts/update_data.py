#!/usr/bin/env python3
"""
최종 확정 전략 v4 — 일간 신호 계산 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
포트폴리오: SPY 40% + QQQ 20% + SGOV 40%
신호:       SPY 60M 롤링 회귀선 괴리율 (일별 데이터 기준)

월 DCA (동적):
  괴리율 ≥ -3%  →  SPY $200 + QQQ $100 + SGOV $200
  괴리율 < -3%  →  SPY $250 + QQQ $125 + SGOV $125

진입 (SGOV → SPY:QQQ = 2:1):
  1차 (괴리율 ≤ -5%)  : 순자산 × 12%
  2차 (괴리율 ≤ -10%) : 순자산 × 12%
  3차 (괴리율 ≤ -15%) : SGOV 잔액 전부 (≈16%)

하락 리밸 (3차 이후, 전술 평단가 기준):
  리밸1 (평단 ≤ -20%) : 외부현금 총자산 × 25%
  리밸2 (평단 ≤ -40%) : 외부현금 총자산 × 25% 추가

완전 리밸 (청산):
  괴리율 +15% 교차 시 → 전술+기본 통합 → 40:20:40 복원 → SAFE
  (분할청산 없음)

수수료: 0.5% 편도
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
백테스트 (1995-2024, 30년):
  최종자산 $1,341,136  CAGR 6.6%  MDD 50.2%  거래 26회  수수료 $5,897
"""
import json, os, urllib.request, time
from datetime import datetime

# ── 전략 파라미터 ────────────────────────────────────────────
REG_WIN         = 1260   # 60개월 ≈ 1260 거래일
W_SPY, W_QQQ, W_SGOV = 0.40, 0.20, 0.40

ENTRY1_DIV  = -5.0   # 1차 진입 괴리율
ENTRY2_DIV  = -10.0  # 2차 진입 괴리율
ENTRY3_DIV  = -15.0  # 3차 진입 괴리율
ENTRY1_PCT  = 0.12   # 1차: 순자산 × 12%
ENTRY2_PCT  = 0.12   # 2차: 순자산 × 12%
# 3차: SGOV 잔액 전부 (≈ 40 - 12 - 12 = 16%)

REBAL_FULL_THR  = +15.0  # 완전 리밸 트리거 (+15% 교차)

DCA_DIV_THR = -3.0                                   # DCA 전환 기준
DCA_BASE    = {'spy': 200, 'qqq': 100, 'sgov': 200} # 기본 DCA
DCA_BULL    = {'spy': 250, 'qqq': 125, 'sgov': 125} # 저평가 DCA

REBAL_DOWN_PCT   = 0.25   # 하락 리밸 외부현금 비율
REBAL_DOWN1_LOSS = -20.0  # 하락 리밸1: 평단 대비 -20%
REBAL_DOWN2_LOSS = -40.0  # 하락 리밸2: 평단 대비 -40%

ASSETS = {
    'SPY': {'stooq': 'spy.us', 'yahoo': 'SPY', 'name': 'S&P 500 (SPY)'},
    'QQQ': {'stooq': 'qqq.us', 'yahoo': 'QQQ', 'name': 'Nasdaq 100 (QQQ)'},
}

# ── 데이터 수집 ──────────────────────────────────────────────
def _stooq(symbol, domain='com', days=1800):
    url = f"https://stooq.{domain}/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/csv,*/*',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode('utf-8')
    except Exception as e:
        print(f"  ⚠ stooq.{domain} ({symbol}): {e}"); return None
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

def _yahoo(symbol, days=1800):
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

def fetch(symbol_s, symbol_y, days=1800):
    for src in [
        lambda: _stooq(symbol_s, 'com', days),
        lambda: _stooq(symbol_s, 'pl',  days),
        lambda: _yahoo(symbol_y,  days),
    ]:
        d = src()
        if d and len(d) > REG_WIN + 20:
            return d
    return []

# ── 선형회귀 ─────────────────────────────────────────────────
def linreg(vals):
    n = len(vals)
    if n < 2: return 0.0, vals[0] if vals else 0.0
    xm = (n - 1) / 2.0; ym = sum(vals) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    den = sum((i - xm) ** 2 for i in range(n))
    slope = num / den if den else 0.0
    return slope, ym - slope * xm

# ── 신호 계산 (최종 전략 v4) ─────────────────────────────────
def calc_signals(closes):
    """
    60M 롤링 회귀선 기반 최종 전략 v4
    반환 필드명: index.html 호환
      trades[]:  index, type, divergence, price, position_after, avg_entry, spy_loss
      states[]:  i, close, predicted, divergence, position, peak
    """
    N = len(closes)
    if N < REG_WIN + 5:
        return [], []

    es = 0          # 진입 단계 (0=SAFE, 1=1차, 2=2차, 3=3차)
    rs = 0          # 하락 리밸 단계 (0, 1, 2)
    prev_over  = False  # +15% 교차 추적

    tac_cost = 0.0  # 전술 포지션 총 투자원가
    tac_sh   = 0.0  # 전술 포지션 정규화 주식수

    trades = []; states = []
    running_peak = closes[0]

    pos_map = {0: 0.0, 1: 0.35, 2: 0.70, 3: 1.0}

    for i in range(REG_WIN, N):
        # SAFE 상태에서만 신고가 갱신
        if es == 0 and closes[i] > running_peak:
            running_peak = closes[i]

        # 60M 롤링 회귀선
        window = closes[i - REG_WIN: i]
        slope, intercept = linreg(window)
        predicted = intercept + slope * REG_WIN

        if predicted == 0:
            states.append({'i': i, 'close': closes[i], 'predicted': None,
                           'divergence': None, 'position': pos_map.get(es, 0.0),
                           'peak': running_peak})
            continue

        divergence = (closes[i] - predicted) / predicted * 100.0

        # 전술 평단가 & 손실률
        spy_avg  = tac_cost / tac_sh if tac_sh > 1e-9 else 0.0
        spy_loss = (closes[i] - spy_avg) / spy_avg * 100 if spy_avg > 0 else 0.0

        action = None

        # ① 완전 리밸: +15% 교차 → 전술+기본 통합 → 40:20:40 → SAFE
        if divergence >= REBAL_FULL_THR and not prev_over:
            tac_cost = 0.0; tac_sh = 0.0
            es = 0; rs = 0
            action = 'REBAL_FULL'

        prev_over = (divergence >= REBAL_FULL_THR)

        # ② 진입 (SAFE 상태에서만)
        if action is None and es == 0 and divergence <= ENTRY1_DIV:
            tac_cost += closes[i] * ENTRY1_PCT
            tac_sh   += ENTRY1_PCT
            es = 1; action = 'ENTRY1'

        elif action is None and es == 1 and divergence <= ENTRY2_DIV:
            tac_cost += closes[i] * ENTRY2_PCT
            tac_sh   += ENTRY2_PCT
            es = 2; action = 'ENTRY2'

        elif action is None and es == 2 and divergence <= ENTRY3_DIV:
            amt = max(0.0, W_SGOV - ENTRY1_PCT - ENTRY2_PCT)  # ≈ 16%
            tac_cost += closes[i] * amt
            tac_sh   += amt
            es = 3; action = 'ENTRY3'

        # ③ 하락 리밸 (3차 이후, 평단가 기준)
        elif action is None and es == 3:
            if rs == 0 and spy_loss <= REBAL_DOWN1_LOSS:
                tac_cost += closes[i] * REBAL_DOWN_PCT
                tac_sh   += REBAL_DOWN_PCT
                rs = 1; action = 'REBAL_DOWN1'
            elif rs == 1 and spy_loss <= REBAL_DOWN2_LOSS:
                tac_cost += closes[i] * REBAL_DOWN_PCT
                tac_sh   += REBAL_DOWN_PCT
                rs = 2; action = 'REBAL_DOWN2'

        cur_pos = pos_map.get(es, 0.0)

        if action:
            trades.append({
                'index':          i,
                'type':           action,
                'divergence':     round(divergence, 2),
                'price':          closes[i],
                'position_after': cur_pos,
                'avg_entry':      round(spy_avg, 2) if spy_avg > 0 else None,
                'spy_loss':       round(spy_loss, 2),
            })

        states.append({
            'i':          i,
            'close':      closes[i],
            'predicted':  round(predicted, 4),
            'divergence': round(divergence, 4),
            'position':   cur_pos,
            'peak':       running_peak,
        })

    return trades, states

# ── MDD 계산 ─────────────────────────────────────────────────
def calc_mdd(series):
    peak = series[0]; worst = 0.0
    for v in series:
        if v > peak: peak = v
        worst = max(worst, (peak - v) / peak * 100 if peak else 0)
    return worst

# ── 현재 신호 판단 ────────────────────────────────────────────
def get_signal(states, trades):
    if not states: return 'NO_SIGNAL'
    last = states[-1]
    pos  = last.get('position', 0.0)
    div  = last.get('divergence')
    if div is None: return 'NO_SIGNAL'

    # +15% 교차 여부 (직전 상태와 비교)
    prev_div = states[-2].get('divergence') if len(states) >= 2 else None
    crossing_up = (prev_div is not None and prev_div < REBAL_FULL_THR
                   and div >= REBAL_FULL_THR)

    if pos > 0.0 and crossing_up:       return 'REBAL_FULL_READY'
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
    'NO_SIGNAL':        '신호 없음',
}

# ── 자산별 페이로드 빌드 ─────────────────────────────────────
def build(ticker, cfg):
    print(f"\n[{ticker}] 데이터 수집 중…")
    recs = fetch(cfg['stooq'], cfg['yahoo'])
    if not recs or len(recs) < REG_WIN + 20:
        print(f"  ✗ 데이터 부족 ({len(recs) if recs else 0}개, 필요 {REG_WIN+20}개)")
        return None

    dates  = [r['date']  for r in recs]
    closes = [r['close'] for r in recs]

    trades, states = calc_signals(closes)
    sig = get_signal(states, trades)

    last     = states[-1] if states else {}
    pos      = last.get('position', 0.0)
    div      = last.get('divergence')
    spy_avg  = None
    spy_loss = None
    # 마지막 진입 평단가 찾기
    for t in reversed(trades):
        if t['type'].startswith('ENTRY') or t['type'].startswith('REBAL_DOWN'):
            spy_avg  = t.get('avg_entry')
            spy_loss = t.get('spy_loss')
            break

    # 시리즈 패딩
    pad    = REG_WIN
    s_div  = [None]   * pad + [s['divergence'] for s in states]
    s_pred = [None]   * pad + [s['predicted']  for s in states]
    s_pos  = [0.0]    * pad + [s['position']   for s in states]
    s_peak = [closes[0]] * pad + [s['peak']    for s in states]

    n_entry = sum(1 for t in trades if t['type'].startswith('ENTRY'))
    n_rebal = sum(1 for t in trades if 'REBAL' in t['type'])

    print(f"  ✓ 신호={sig} | 괴리율={div}% | 포지션={pos} | 진입{n_entry}회 | 리밸{n_rebal}회")
    print(f"    종가={last.get('close')} | 예측가={last.get('predicted')} | 신고가={last.get('peak')}")

    return {
        'ticker': ticker,
        'name':   cfg['name'],
        'dates':  dates,
        'closes': closes,
        # 시리즈 (index.html 호환)
        'divergence': s_div,
        'predicted':  s_pred,
        'position':   s_pos,
        'peak':       s_peak,
        # 거래 기록
        'trades':        trades,
        'latest_trades': trades[-15:],
        # 현재 상태 (index.html current.* 키)
        'current': {
            'signal':     sig,
            'msg':        SIG_MSG.get(sig, sig),
            'position':   pos,
            'divergence': div,
            'close':      last.get('close'),
            'predicted':  last.get('predicted'),
            'peak':       last.get('peak'),
            'date':       dates[-1] if dates else None,
            'spy_avg':    spy_avg,
            'spy_loss':   spy_loss,
        },
        # 전략 파라미터 (앱 표시용)
        'params': {
            'portfolio':   'SPY40+QQQ20+SGOV40',
            'signal':      'SPY 60M 롤링 회귀선 괴리율',
            'reg_win':     REG_WIN,
            'entry_div':   [ENTRY1_DIV, ENTRY2_DIV, ENTRY3_DIV],
            'entry_pct':   [ENTRY1_PCT*100, ENTRY2_PCT*100, '잔액전부(≈16%)'],
            'rebal_full':  REBAL_FULL_THR,
            'rebal_down':  [REBAL_DOWN1_LOSS, REBAL_DOWN2_LOSS, REBAL_DOWN_PCT*100],
            'dca_base':    DCA_BASE,
            'dca_bull':    DCA_BULL,
            'dca_div_thr': DCA_DIV_THR,
        },
    }

# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=" * 62)
    print("최종 전략 v4 — 60M 롤링 회귀 | SPY40+QQQ20+SGOV40")
    print(f"실행: {datetime.utcnow().isoformat()} UTC")
    print(f"회귀선: {REG_WIN}거래일 | 리밸: +{REBAL_FULL_THR}% 통합리밸")
    print("=" * 62)

    output = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name':      'Final Strategy v4 — SPY40+QQQ20+SGOV40 | 60M Rolling Regression',
            'version':   'v4',
            'portfolio': {'spy': W_SPY, 'qqq': W_QQQ, 'sgov': W_SGOV},
            'dca': {
                'base': DCA_BASE,
                'bull': DCA_BULL,
                'div_threshold': DCA_DIV_THR,
                'note': f'괴리율 {DCA_DIV_THR}% 이하 시 bull DCA 적용',
            },
            'entry': [
                {'level': 1, 'div': ENTRY1_DIV, 'pct': f'{ENTRY1_PCT*100:.0f}%'},
                {'level': 2, 'div': ENTRY2_DIV, 'pct': f'{ENTRY2_PCT*100:.0f}%'},
                {'level': 3, 'div': ENTRY3_DIV, 'pct': 'SGOV잔액전부(≈16%)'},
            ],
            'exit': '분할청산없음 — +15% 교차 시 통합리밸',
            'rebal_full': {
                'trigger': f'+{REBAL_FULL_THR}% 교차',
                'action':  '전술+기본 통합 → 40:20:40 복원 → SAFE',
                'limit':   '제한없음',
            },
            'rebal_down': {
                'trigger1': f'평단 {REBAL_DOWN1_LOSS}%',
                'trigger2': f'평단 {REBAL_DOWN2_LOSS}%',
                'invest':   f'외부현금 총자산×{REBAL_DOWN_PCT*100:.0f}%',
                'source':   '외부현금(무이자)',
            },
            'backtest': {
                'period': '1995-2024 (30년)',
                'final':  1341136,
                'cagr':   6.6,
                'mdd':    50.2,
                'trades': 26,
                'fee':    5897,
            },
        },
        'assets': {},
    }

    out_path = 'docs/data.json'
    for ticker, cfg in ASSETS.items():
        try:
            result = build(ticker, cfg)
        except Exception as e:
            print(f"  ✗ {ticker} 예외: {e}"); result = None
        if result:
            output['assets'][ticker] = result
        time.sleep(1)

    if not output['assets']:
        print("\n❌ 수집 실패 — 기존 data.json 유지")
        if os.path.exists(out_path): return True
        output['error'] = 'fetch_failed'

    # 일부 실패 시 이전 데이터 병합
    if os.path.exists(out_path) and len(output['assets']) < len(ASSETS):
        try:
            with open(out_path) as f:
                prev = json.load(f)
            for t in ASSETS:
                if t not in output['assets'] and t in prev.get('assets', {}):
                    output['assets'][t] = prev['assets'][t]
                    print(f"  ↩ {t}: 이전 데이터 유지")
        except Exception as e:
            print(f"  ⚠ 병합 실패: {e}")

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    size = os.path.getsize(out_path)
    print(f"\n✅ 저장 완료: {out_path} ({size // 1024}KB)")
    return True

if __name__ == '__main__':
    exit(0 if main() else 1)
