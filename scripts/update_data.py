#!/usr/bin/env python3
"""
최종 확정 전략 — 일간 신호 계산 스크립트
포트폴리오: SPY 40% + QQQ 20% + SGOV 40%
신호:       SPY 36M 롤링 회귀선 괴리율
진입:       -5% / -10% / -15%  (순자산 12%/12%/SGOV잔액)
청산:       +5%=50%, +10%=전량
과열 리밸:  +20% 교차 시 → 40:20:40 복원 (제한없음)
하락 리밸:  평단가 -20%/-40% → 외부현금 25%+25%
"""

import json, urllib.request, time, os
from datetime import datetime

# ── 전략 파라미터 ────────────────────────────────────────────
REG_WIN_DAYS    = 756    # 36개월 (거래일 ≈ 756)
W_SPY, W_QQQ, W_SGOV = 0.40, 0.20, 0.40
ENTRY1_DIV = -5.0
ENTRY2_DIV = -10.0
ENTRY3_DIV = -15.0
EXIT1_DIV  = +5.0
EXIT2_DIV  = +10.0
REBAL_DIV  = +20.0
ENTRY1_PCT = 0.12
ENTRY2_PCT = 0.12
REBAL_DOWN_PCT   = 0.25
REBAL_DOWN1_LOSS = -20.0
REBAL_DOWN2_LOSS = -40.0


# ── 데이터 수집 ──────────────────────────────────────────────
def fetch_stooq(symbol, days=1000):
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode('utf-8')
    except Exception as e:
        print(f"  Stooq 실패 ({symbol}): {e}"); return None
    lines = text.strip().split('\n')
    if len(lines) < 2: return None
    hdr = lines[0].split(',')
    try: di, ci = hdr.index('Date'), hdr.index('Close')
    except ValueError: return None
    recs = []
    for ln in lines[1:]:
        p = ln.split(',')
        if len(p) > max(di, ci):
            try: recs.append({'date': p[di], 'close': float(p[ci])})
            except ValueError: pass
    return recs[-days:] if len(recs) > days else recs

def fetch_yahoo(symbol, days=1000):
    end = int(time.time()); start = end - days * 86400 * 2
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={start}&period2={end}&interval=1d")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f"  Yahoo 실패 ({symbol}): {e}"); return None
    try:
        res = data['chart']['result'][0]
        ts  = res['timestamp']
        cl  = res['indicators']['quote'][0]['close']
    except (KeyError, IndexError): return None
    recs = []
    for t, c in zip(ts, cl):
        if c is not None:
            recs.append({'date': datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'),
                         'close': round(c, 4)})
    return recs[-days:] if len(recs) > days else recs

def get_prices(stooq_sym, yahoo_sym, days=1000):
    print(f"  수집: {stooq_sym} ...")
    d = fetch_stooq(stooq_sym, days)
    if d and len(d) > 200:
        print(f"    Stooq {len(d)}개"); return d
    d = fetch_yahoo(yahoo_sym, days)
    if d and len(d) > 200:
        print(f"    Yahoo {len(d)}개"); return d
    print(f"    수집 실패"); return []


# ── 36M 롤링 회귀선 ─────────────────────────────────────────
def linreg(vals):
    n = len(vals)
    if n < 2: return 0.0, vals[0] if vals else 0.0
    xm = (n - 1) / 2.0; ym = sum(vals) / n
    num = sum((i - xm) * (v - ym) for i, v in enumerate(vals))
    den = sum((i - xm) ** 2 for i in range(n))
    if den == 0: return 0.0, ym
    s = num / den
    return s, ym - s * xm

def calc_divergence(closes, win=REG_WIN_DAYS):
    divs = [None] * len(closes)
    preds = [None] * len(closes)
    for i in range(win, len(closes)):
        w = closes[i - win:i]
        s, b = linreg(w)
        pred = b + s * win
        divs[i]  = round((closes[i] - pred) / pred * 100, 4) if pred else None
        preds[i] = round(pred, 4)
    return divs, preds


# ── 신호 시뮬레이션 ──────────────────────────────────────────
def simulate(closes, dates, divs):
    es = 0; rs = 0; exit_stage = 0
    prev_over20 = False
    tac_cost = 0.0; tac_sh = 0.0
    trades = []; states = []

    for i in range(len(closes)):
        div = divs[i]; close = closes[i]
        if div is None:
            states.append({'date': dates[i], 'close': close, 'div': None,
                           'es': es, 'rs': rs, 'tac': 0.0,
                           'spy_avg': 0.0, 'spy_loss': 0.0})
            continue

        spy_avg  = tac_cost / tac_sh if tac_sh > 1e-9 else 0.0
        spy_loss = (close - spy_avg) / spy_avg * 100 if spy_avg > 0 else 0.0
        action = None

        # 과열 리밸 (+20% 교차, SAFE)
        if div >= REBAL_DIV and es == 0 and not prev_over20:
            action = 'REBAL_HOT'
        prev_over20 = (div >= REBAL_DIV)

        # 분할 청산
        if action is None and es > 0 and exit_stage == 0 and div >= EXIT1_DIV:
            tac_cost *= 0.50; tac_sh *= 0.50
            exit_stage = 1; action = 'EXIT1'
        elif action is None and es > 0 and exit_stage == 1 and div >= EXIT2_DIV:
            tac_cost = 0.0; tac_sh = 0.0
            es = 0; rs = 0; exit_stage = 0; action = 'EXIT2'

        # 진입
        elif action is None and es == 0 and div <= ENTRY1_DIV:
            tac_cost += close * ENTRY1_PCT; tac_sh += ENTRY1_PCT
            es = 1; exit_stage = 0; action = 'ENTRY1'
        elif action is None and es == 1 and div <= ENTRY2_DIV:
            tac_cost += close * ENTRY2_PCT; tac_sh += ENTRY2_PCT
            es = 2; action = 'ENTRY2'
        elif action is None and es == 2 and div <= ENTRY3_DIV:
            amt = max(0.0, W_SGOV - ENTRY1_PCT - ENTRY2_PCT)
            tac_cost += close * amt; tac_sh += amt
            es = 3; action = 'ENTRY3'

        # 하락 리밸 (평단가 기준, 3차 이후)
        elif action is None and es == 3:
            if rs == 0 and spy_loss <= REBAL_DOWN1_LOSS:
                tac_cost += close * REBAL_DOWN_PCT; tac_sh += REBAL_DOWN_PCT
                rs = 1; action = 'REBAL_DOWN1'
            elif rs == 1 and spy_loss <= REBAL_DOWN2_LOSS:
                tac_cost += close * REBAL_DOWN_PCT; tac_sh += REBAL_DOWN_PCT
                rs = 2; action = 'REBAL_DOWN2'

        if action:
            trades.append({'date': dates[i], 'type': action,
                           'div': round(div, 2), 'close': round(close, 2),
                           'es': es, 'spy_avg': round(spy_avg, 2),
                           'spy_loss': round(spy_loss, 2)})
        states.append({'date': dates[i], 'close': close,
                       'div': round(div, 4), 'es': es, 'rs': rs,
                       'tac': round(tac_sh, 4),
                       'spy_avg': round(spy_avg, 2),
                       'spy_loss': round(spy_loss, 2)})
    return trades, states


def get_signal(state, trades):
    es = state['es']; rs = state.get('rs', 0)
    div = state['div']; spy_loss = state.get('spy_loss', 0)
    if div is None: return 'NO_SIGNAL'
    if es == 0 and div >= REBAL_DIV: return 'REBAL_HOT_READY'
    if es > 0:
        if div >= EXIT2_DIV: return 'EXIT2_READY'
        if div >= EXIT1_DIV: return 'EXIT1_READY'
        if es == 3:
            if rs == 0 and spy_loss <= REBAL_DOWN1_LOSS: return 'REBAL_DOWN1_READY'
            if rs == 1 and spy_loss <= REBAL_DOWN2_LOSS: return 'REBAL_DOWN2_READY'
        return 'HOLDING'
    if es == 0:
        if div <= ENTRY1_DIV: return 'ENTRY1_READY'
    if es == 1 and div <= ENTRY2_DIV: return 'ENTRY2_READY'
    if es == 2 and div <= ENTRY3_DIV: return 'ENTRY3_READY'
    return 'WAITING'


SIGNAL_MSG = {
    'WAITING':           '관망 중',
    'ENTRY1_READY':      '⚡ 1차 진입 — SGOV에서 순자산×12% → SPY:QQQ=2:1',
    'ENTRY2_READY':      '⚡ 2차 진입 — SGOV에서 순자산×12% → SPY:QQQ=2:1',
    'ENTRY3_READY':      '⚡ 3차 진입 — SGOV 잔액 전부 → SPY:QQQ=2:1',
    'HOLDING':           '전술 포지션 보유 중',
    'EXIT1_READY':       '💰 청산1 — 전술 포지션 50% 매도',
    'EXIT2_READY':       '💰 청산2 — 전술 포지션 전량 → SAFE',
    'REBAL_HOT_READY':   '⚖️ 과열 리밸 — 전체 포트 40:20:40 복원',
    'REBAL_DOWN1_READY': '📉 하락 리밸1 — 외부현금 총자산×25%',
    'REBAL_DOWN2_READY': '📉 하락 리밸2 — 외부현금 총자산×25% 추가',
    'NO_SIGNAL':         '신호 없음',
}

ASSETS = {
    'SPY': {'stooq': 'spy.us', 'yahoo': 'SPY', 'name': 'S&P 500 (SPY)'},
    'QQQ': {'stooq': 'qqq.us', 'yahoo': 'QQQ', 'name': 'Nasdaq 100 (QQQ)'},
}

def build(ticker, cfg):
    print(f"\n[{ticker}]")
    recs = get_prices(cfg['stooq'], cfg['yahoo'])
    if not recs or len(recs) < REG_WIN_DAYS + 30:
        print(f"  데이터 부족"); return None
    dates  = [r['date']  for r in recs]
    closes = [r['close'] for r in recs]
    divs, preds = calc_divergence(closes)
    trades, states = simulate(closes, dates, divs)
    last = states[-1]
    sig  = get_signal(last, trades)
    print(f"  괴리율={last['div']:+.1f if last['div'] else 'N/A'}%, 신호={sig}, 진입단계={last['es']}")
    return {
        'ticker': ticker, 'name': cfg['name'],
        'dates': dates, 'closes': closes,
        'divergence': divs, 'predicted': preds,
        'es_series':  [s['es']  for s in states],
        'tac_series': [s['tac'] for s in states],
        'trades': trades, 'latest_trades': trades[-15:],
        'current': {
            'signal': sig, 'msg': SIGNAL_MSG.get(sig, sig),
            'date': last['date'], 'close': last['close'],
            'div': last['div'], 'es': last['es'], 'rs': last.get('rs', 0),
            'spy_avg': last['spy_avg'], 'spy_loss': last['spy_loss'],
            'tac': last['tac'],
        },
        'params': {
            'reg_days': REG_WIN_DAYS,
            'weights': {'spy': W_SPY, 'qqq': W_QQQ, 'sgov': W_SGOV},
            'entry_div': [ENTRY1_DIV, ENTRY2_DIV, ENTRY3_DIV],
            'entry_pct': [ENTRY1_PCT, ENTRY2_PCT, 'sgov잔액'],
            'exit_div': [EXIT1_DIV, EXIT2_DIV],
            'rebal_hot': REBAL_DIV,
            'rebal_down': [REBAL_DOWN1_LOSS, REBAL_DOWN2_LOSS, REBAL_DOWN_PCT],
        }
    }


def main():
    print("=" * 60)
    print("최종 확정 전략 — 일간 신호 업데이트")
    print(f"{datetime.utcnow().isoformat()} UTC")
    print("=" * 60)

    output = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name': 'Final Strategy v2 — SPY40+QQQ20+SGOV40 | 36M Rolling Regression',
            'weights': {'spy': W_SPY, 'qqq': W_QQQ, 'sgov': W_SGOV},
            'dca': {'spy': 200, 'qqq': 100, 'sgov': 200, 'total': 500},
            'entry': [
                {'level': 1, 'div': ENTRY1_DIV, 'pct': f'{ENTRY1_PCT*100:.0f}%'},
                {'level': 2, 'div': ENTRY2_DIV, 'pct': f'{ENTRY2_PCT*100:.0f}%'},
                {'level': 3, 'div': ENTRY3_DIV, 'pct': 'SGOV잔액전부(≈16%)'},
            ],
            'exit': [
                {'level': 1, 'div': EXIT1_DIV, 'pct': '50%'},
                {'level': 2, 'div': EXIT2_DIV, 'pct': '전량'},
            ],
            'rebal_hot': {'trigger': f'+{REBAL_DIV}% 교차', 'limit': '제한없음'},
            'rebal_down': {'t1': REBAL_DOWN1_LOSS, 't2': REBAL_DOWN2_LOSS,
                           'pct': f'{REBAL_DOWN_PCT*100:.0f}%', 'source': '외부현금(무이자)'},
            'signal': 'SPY 36M 롤링 회귀선 괴리율',
        },
        'assets': {}
    }

    out_path = 'docs/data.json'
    for ticker, cfg in ASSETS.items():
        try:
            r = build(ticker, cfg)
        except Exception as e:
            print(f"  오류 ({ticker}): {e}"); r = None
        if r:
            output['assets'][ticker] = r
        time.sleep(1)

    if not output['assets']:
        if os.path.exists(out_path):
            print("기존 data.json 유지"); return True
        return False

    if os.path.exists(out_path) and len(output['assets']) < len(ASSETS):
        try:
            with open(out_path) as f:
                prev = json.load(f)
            for t in ASSETS:
                if t not in output['assets'] and t in prev.get('assets', {}):
                    output['assets'][t] = prev['assets'][t]
                    print(f"  {t}: 이전 데이터 유지")
        except Exception as e:
            print(f"  병합 실패: {e}")

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    print(f"\n✅ {out_path} ({len(json.dumps(output))/1024:.1f} KB)")
    return True

if __name__ == '__main__':
    exit(0 if main() else 1)
