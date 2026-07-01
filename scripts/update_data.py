#!/usr/bin/env python3
"""
최종 전략 v5 — 400일 SMA 기반 일간 신호 계산 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
데이터:    일별 수집 → 일별 그대로 사용 (리샘플링 없음)
신호:      SPY 400일 단순이동평균(SMA) 괴리율

포트폴리오: SPY 40% + QQQ 20% + SGOV 40%

월 DCA (동적, 매월 첫 거래일):
  괴리율(전일 기준) ≥ -3%  →  SPY $200 + QQQ $100 + SGOV $200
  괴리율(전일 기준) < -3%  →  SPY $250 + QQQ $125 + SGOV $125

진입:  1차(-5%) / 2차(-10%) / 3차(-15%)
리밸:  +15% 교차 → 전술+기본 통합 → 40:20:40 → SAFE
       (쿨다운 60거래일: 재발동까지 최소 60거래일 대기 — SMA 노이즈로 인한 과매매 방지)
하락:  평단 -20%/-40% → 외부현금 25%+25%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
백테스트(1994-08~2026-07, 22년): $1,858,102 | CAGR 10.4% | MDD 54.4%
  (SPY40:QQQ20:SGOV40 | 진입17회 | 리밸29회 | 쿨다운3개월)
  ※ 60M(월별) 방식 대비 CAGR +3.9%p 높으나 MDD +4.5%p, 거래빈도 2.4배
"""
import json, os, urllib.request, time
from datetime import datetime

# ── 전략 파라미터 ────────────────────────────────────────────
SMA_WIN        = 400     # 400 거래일 단순이동평균
W_SPY, W_QQQ, W_SGOV = 0.40, 0.20, 0.40

ENTRY1_DIV  = -5.0
ENTRY2_DIV  = -10.0
ENTRY3_DIV  = -15.0
ENTRY1_PCT  = 0.12
ENTRY2_PCT  = 0.12

REBAL_FULL_THR  = +15.0
REBAL_COOLDOWN  = 60      # 거래일. 리밸 후 재발동까지 최소 대기 (노이즈 방지)
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
def fetch_stooq_daily(symbol, days=9000):
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
        try:
            d = p[di]
            if d < '1993-01-01':   # 1993년 이전은 월별 역산 데이터라 제외
                continue
            recs.append({'date': d, 'close': float(p[ci])})
        except: pass
    return recs[-days:] if len(recs) > days else recs

def fetch_yahoo_daily(symbol, days=9000):
    end = int(time.time()); start = end - days * 86400
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

def fetch_daily(symbol_s, symbol_y, days=9000):
    print(f"  일별 수집: {symbol_s}…")
    d = fetch_stooq_daily(symbol_s, days)
    if d and len(d) > SMA_WIN + 100:
        print(f"    stooq {len(d)}개 ({d[0]['date']} ~ {d[-1]['date']})")
        return d
    print(f"  Yahoo fallback: {symbol_y}…")
    d = fetch_yahoo_daily(symbol_y, days)
    if d and len(d) > SMA_WIN + 100:
        print(f"    yahoo {len(d)}개 ({d[0]['date']} ~ {d[-1]['date']})")
        return d
    return []

# ── 400일 SMA 신호 계산 (일별 데이터 그대로) ──────────────────
def calc_sma(closes, i, win):
    if i < win: return None
    return sum(closes[i-win:i]) / win

def calc_signals(dates, closes):
    """
    일별 종가 배열에 대해 400일 SMA 괴리율 기반 신호 계산.
    반환: trades(거래 리스트), states(일별 상태 리스트, SMA 계산 가능 구간부터)
    """
    N = len(closes)
    if N < SMA_WIN + 30: return [], []

    # 월별 DCA 발생일(각 달의 첫 거래일) 인덱스
    month_first = {}
    for i, dt in enumerate(dates):
        ym = dt[:7]
        if ym not in month_first:
            month_first[ym] = i
    dca_days = set(month_first.values())

    es = 0; rs = 0; prev_over = False
    tac_spy_sh = 0.0; tac_spy_cost = 0.0
    tac_qqq_sh = 0.0; tac_qqq_cost = 0.0
    trades = []; states = []
    running_peak = closes[0]
    last_rebal_i = -999999
    pos_map = {0: 0.0, 1: 0.35, 2: 0.70, 3: 1.0}

    for i in range(SMA_WIN, N):
        if es == 0 and closes[i] > running_peak:
            running_peak = closes[i]

        s = calc_sma(closes, i, SMA_WIN)
        if not s:
            states.append({'i': i, 'close': closes[i], 'predicted': None,
                           'divergence': None, 'position': pos_map.get(es, 0.0),
                           'peak': running_peak})
            continue

        divergence = (closes[i] - s) / s * 100.0
        spy_avg  = tac_spy_cost / tac_spy_sh if tac_spy_sh > 1e-9 else 0.0
        spy_loss = (closes[i] - spy_avg) / spy_avg * 100 if spy_avg > 0 else 0.0
        action = None

        # ① 완전 리밸: +15% 교차 (쿨다운 적용 — SMA 노이즈로 인한 과매매 방지)
        can_rebal = (i - last_rebal_i) >= REBAL_COOLDOWN
        if divergence >= REBAL_FULL_THR and not prev_over and can_rebal:
            tac_spy_sh = 0.0; tac_spy_cost = 0.0
            tac_qqq_sh = 0.0; tac_qqq_cost = 0.0
            es = 0; rs = 0; action = 'REBAL_FULL'
            last_rebal_i = i
        prev_over = (divergence >= REBAL_FULL_THR)

        # ② 진입
        if action is None and es == 0 and divergence <= ENTRY1_DIV:
            inv = closes[i] * ENTRY1_PCT
            tac_spy_sh += inv*(2/3)/closes[i]; tac_spy_cost += inv*(2/3)
            tac_qqq_sh += inv*(1/3)/closes[i]; tac_qqq_cost += inv*(1/3)
            es = 1; action = 'ENTRY1'
        elif action is None and es == 1 and divergence <= ENTRY2_DIV:
            inv = closes[i] * ENTRY2_PCT
            tac_spy_sh += inv*(2/3)/closes[i]; tac_spy_cost += inv*(2/3)
            tac_qqq_sh += inv*(1/3)/closes[i]; tac_qqq_cost += inv*(1/3)
            es = 2; action = 'ENTRY2'
        elif action is None and es == 2 and divergence <= ENTRY3_DIV:
            amt = max(0.0, W_SGOV - ENTRY1_PCT - ENTRY2_PCT)
            inv = closes[i] * amt
            tac_spy_sh += inv*(2/3)/closes[i]; tac_spy_cost += inv*(2/3)
            tac_qqq_sh += inv*(1/3)/closes[i]; tac_qqq_cost += inv*(1/3)
            es = 3; action = 'ENTRY3'

        # ③ 하락 리밸 (3차 이후)
        elif action is None and es == 3:
            if rs == 0 and spy_loss <= REBAL_DOWN1_LOSS:
                inv = closes[i] * REBAL_DOWN_PCT
                tac_spy_sh += inv*(2/3)/closes[i]; tac_spy_cost += inv*(2/3)
                tac_qqq_sh += inv*(1/3)/closes[i]; tac_qqq_cost += inv*(1/3)
                rs = 1; action = 'REBAL_DOWN1'
            elif rs == 1 and spy_loss <= REBAL_DOWN2_LOSS:
                inv = closes[i] * REBAL_DOWN_PCT
                tac_spy_sh += inv*(2/3)/closes[i]; tac_spy_cost += inv*(2/3)
                tac_qqq_sh += inv*(1/3)/closes[i]; tac_qqq_cost += inv*(1/3)
                rs = 2; action = 'REBAL_DOWN2'

        cur_pos = pos_map.get(es, 0.0)
        if action:
            trades.append({'index': i, 'date': dates[i], 'type': action,
                           'divergence': round(divergence, 2),
                           'price': closes[i],
                           'position_after': cur_pos,
                           'avg_entry': round(spy_avg, 2) if spy_avg > 0 else None,
                           'spy_loss': round(spy_loss, 2),
                           'is_dca_day': i in dca_days})
        states.append({'i': i, 'close': closes[i], 'predicted': round(s, 4),
                       'divergence': round(divergence, 4), 'position': cur_pos,
                       'peak': running_peak, 'is_dca_day': i in dca_days})

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

    daily = fetch_daily(cfg['stooq'], cfg['yahoo'], days=9000)
    if not daily:
        print(f"  ✗ 데이터 수집 실패")
        return None
    if len(daily) < SMA_WIN + 30:
        print(f"  ✗ 데이터 부족 ({len(daily)}개, 필요 {SMA_WIN+30}개)")
        return None

    dates  = [r['date']  for r in daily]
    closes = [r['close'] for r in daily]
    print(f"  일별 데이터: {len(daily)}개 ({dates[0]} ~ {dates[-1]})")

    trades, states = calc_signals(dates, closes)
    sig = get_signal(states)

    last = states[-1] if states else {}
    pos  = last.get('position', 0.0)
    div  = last.get('divergence')
    pred = last.get('predicted')
    peak = last.get('peak')

    latest_close = closes[-1]
    latest_date  = dates[-1]

    pad    = SMA_WIN
    s_div  = [None]         * pad + [s['divergence'] for s in states]
    s_pred = [None]         * pad + [s['predicted']  for s in states]
    s_pos  = [0.0]          * pad + [s['position']   for s in states]
    s_peak = [closes[0]]    * pad + [s['peak']       for s in states]

    n_entry = sum(1 for t in trades if t['type'].startswith('ENTRY'))
    n_rebal = sum(1 for t in trades if 'REBAL' in t['type'])

    print(f"  ✓ 신호={sig} | 400일SMA괴리율={div}% | 예측가(SMA)={pred}")
    print(f"    현재가={latest_close} ({latest_date}) | 진입{n_entry}회 | 리밸{n_rebal}회")

    return {
        'ticker': ticker, 'name': cfg['name'],

        # 일별 시리즈 (SMA/괴리율 차트, 400일 워밍업 이후부터 유효)
        'dates':      dates,
        'closes':     closes,
        'divergence': s_div,
        'predicted':  s_pred,
        'position':   s_pos,
        'peak':       s_peak,

        'trades':        trades,
        'latest_trades': trades[-15:],

        'current': {
            'signal':       sig,
            'msg':          SIG_MSG.get(sig, sig),
            'position':     pos,
            'divergence':   div,           # 400일 SMA 기준 괴리율 (신호 판단, 일별 최신)
            'close':        latest_close,
            'predicted':    pred,          # 400일 SMA 값
            'peak':         peak,
            'date':         latest_date,
        },

        'params': {
            'portfolio':     'SPY40+QQQ20+SGOV40',
            'signal':        'SPY 400일 단순이동평균(SMA) 괴리율 (일별)',
            'data_freq':     'daily (no resample)',
            'sma_win':       SMA_WIN,
            'entry_div':     [ENTRY1_DIV, ENTRY2_DIV, ENTRY3_DIV],
            'entry_pct':     [ENTRY1_PCT*100, ENTRY2_PCT*100, '잔액전부(≈16%)'],
            'rebal_full':    REBAL_FULL_THR,
            'rebal_cooldown':f'{REBAL_COOLDOWN}거래일',
            'rebal_down':    [REBAL_DOWN1_LOSS, REBAL_DOWN2_LOSS, REBAL_DOWN_PCT*100],
            'dca_base':      DCA_BASE,
            'dca_bull':      DCA_BULL,
            'dca_div_thr':   DCA_DIV_THR,
        },
    }

# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("최종 전략 v5 — 400일 SMA (일별) | 신호=SPY 400SMA 괴리율")
    print(f"실행: {datetime.utcnow().isoformat()} UTC")
    print(f"SMA={SMA_WIN}거래일 | 리밸=+{REBAL_FULL_THR}%(쿨다운{REBAL_COOLDOWN}거래일) | DCA동적")
    print("=" * 65)

    output = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name':    'Final v5 — SPY40+QQQ20+SGOV40 | 400-Day SMA (Daily)',
            'version': 'v5',
            'note':    '일별 데이터 그대로 사용, 리샘플링 없음. 400일 SMA 괴리율 기준',
            'portfolio': {'spy': W_SPY, 'qqq': W_QQQ, 'sgov': W_SGOV},
            'dca': {'base': DCA_BASE, 'bull': DCA_BULL, 'div_thr': DCA_DIV_THR},
            'entry': [
                {'level':1,'div':ENTRY1_DIV,'pct':f'{ENTRY1_PCT*100:.0f}%'},
                {'level':2,'div':ENTRY2_DIV,'pct':f'{ENTRY2_PCT*100:.0f}%'},
                {'level':3,'div':ENTRY3_DIV,'pct':'SGOV잔액전부(≈16%)'},
            ],
            'rebal_full': {'trigger':f'+{REBAL_FULL_THR}% 교차 (쿨다운{REBAL_COOLDOWN}거래일)',
                           'action':'전술+기본통합→40:20:40→SAFE'},
            'rebal_down': {'t1':REBAL_DOWN1_LOSS,'t2':REBAL_DOWN2_LOSS,
                           'pct':f'{REBAL_DOWN_PCT*100:.0f}%'},
            'backtest': {'final':1858102,'cagr':10.4,'mdd':54.4,'trades':46,
                        'period':'1994-08~2026-07 (22년)'},
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
