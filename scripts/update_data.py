#!/usr/bin/env python3
"""
최종 확정 전략 — 일간 신호 계산 스크립트
포트폴리오: SPY 40% + QQQ 20% + SGOV 40%
신호:       SPY 60M 롤링 회귀선 괴리율 (1260 거래일)
진입:       -5% / -10% / -15%
청산:       +5%=50%, +10%=전량
과열 리밸:  +20% 교차 → 40:20:40 복원 (제한없음)
하락 리밸:  평단가 -20%/-40% → 외부현금 25%+25%

※ current / trades / 시리즈 키명은 원본 app.js 호환 유지
"""
import json, os, urllib.request, time
from datetime import datetime

# ── 전략 파라미터 ────────────────────────────────────────────
REG_WIN    = 1260   # 60개월 (거래일 ≈ 1260)
ENTRY1     = -5.0
ENTRY2     = -10.0
ENTRY3     = -15.0
EXIT1_THR  = +5.0   # 분할 청산 1차 (+5%, 50%)
EXIT2_THR  = +10.0  # 분할 청산 2차 (+10%, 전량)
REBAL_THR  = +15.0  # 과열 리밸 트리거

ENTRY1_PCT = 0.12   # 순자산×12%
ENTRY2_PCT = 0.12
# 3차: SGOV 잔액 전부 ≈ 16%
REBAL_DOWN_PCT   = 0.25
REBAL_DOWN1_LOSS = -20.0  # 평단가 기준
REBAL_DOWN2_LOSS = -40.0

ASSETS = {
    'SPY': {'stooq':'spy.us','yahoo':'SPY','name':'S&P 500 (SPY)'},
    'QQQ': {'stooq':'qqq.us','yahoo':'QQQ','name':'Nasdaq 100 (QQQ)'},
}

# ── 데이터 수집 ──────────────────────────────────────────────
def _stooq(symbol, domain='com', days=1100):
    url = f"https://stooq.{domain}/q/d/l/?s={symbol}&i=d"
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
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

def _yahoo(symbol, days=1100):
    end = int(time.time()); start = end - days * 86400 * 2
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={start}&period2={end}&interval=1d")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode('utf-8'))
        res = data['chart']['result'][0]
        ts  = res['timestamp']
        cl  = res['indicators']['quote'][0]['close']
        recs = [{'date': datetime.utcfromtimestamp(t).strftime('%Y-%m-%d'), 'close': round(c,4)}
                for t, c in zip(ts, cl) if c is not None]
        return recs[-days:] if len(recs) > days else recs
    except Exception as e:
        print(f"  ⚠ yahoo ({symbol}): {e}"); return None

def fetch(symbol_s, symbol_y, days=1100):
    for src in [lambda: _stooq(symbol_s,'com',days),
                lambda: _stooq(symbol_s,'pl',days),
                lambda: _yahoo(symbol_y, days)]:
        d = src()
        if d and len(d) > 100:
            return d
    return []

# ── 선형회귀 ─────────────────────────────────────────────────
def linreg(vals):
    n = len(vals)
    if n < 2: return 0.0, vals[0] if vals else 0.0
    xm = (n-1)/2.0; ym = sum(vals)/n
    num = sum((i-xm)*(v-ym) for i,v in enumerate(vals))
    den = sum((i-xm)**2 for i in range(n))
    slope = num/den if den else 0.0
    return slope, ym-slope*xm

# ── 신호 계산 (최종 전략, 원본 호환 필드명) ─────────────────
def calc_signals(closes):
    """
    36M 롤링 회귀선 기반 최종 전략
    반환 필드명: 원본 app.js 호환
      trades[]:  index, type, divergence, price, position_after
      states[]:  i, close, predicted, divergence, position, peak
    """
    N = len(closes)
    if N < REG_WIN + 5:
        return [], []

    # 진입 상태
    es = 0          # 진입 단계 (0=SAFE, 1=1차, 2=2차, 3=3차)
    rs = 0          # 하락 리밸 단계
    exit_stage = 0  # 분할 청산 (0=미진행, 1=1차완료)
    prev_over20 = False

    # 전술 포지션 평단가 추적
    tac_cost = 0.0; tac_sh = 0.0

    trades = []; states = []
    running_peak = closes[0]

    for i in range(REG_WIN, N):
        # SAFE 상태에서만 신고가 갱신
        if es == 0 and closes[i] > running_peak:
            running_peak = closes[i]

        # 36M 롤링 회귀선
        window = closes[i - REG_WIN: i]
        slope, intercept = linreg(window)
        predicted = intercept + slope * REG_WIN

        if predicted == 0:
            states.append({'i': i, 'close': closes[i], 'predicted': None,
                           'divergence': None, 'position': es/10.0,  # 호환용
                           'peak': running_peak})
            continue

        divergence = (closes[i] - predicted) / predicted * 100.0

        # 평단가 & 손실률
        spy_avg  = tac_cost / tac_sh if tac_sh > 1e-9 else 0.0
        spy_loss = (closes[i] - spy_avg) / spy_avg * 100 if spy_avg > 0 else 0.0

        action = None

        # 1) 과열 리밸 (+20% 교차, SAFE 상태에서만)
        if divergence >= REBAL_THR and es == 0 and not prev_over20:
            action = 'REBAL_HOT'
        prev_over20 = (divergence >= REBAL_THR)

        # 2) 분할 청산
        if action is None and es > 0 and exit_stage == 0 and divergence >= EXIT1_THR:
            tac_cost *= 0.50; tac_sh *= 0.50
            exit_stage = 1; action = 'EXIT'   # app.js 호환: EXIT

        elif action is None and es > 0 and exit_stage == 1 and divergence >= EXIT2_THR:
            tac_cost = 0.0; tac_sh = 0.0
            es = 0; rs = 0; exit_stage = 0; action = 'EXIT2'

        # 3) 진입
        elif action is None and es == 0 and divergence <= ENTRY1:
            tac_cost += closes[i] * ENTRY1_PCT
            tac_sh   += ENTRY1_PCT
            es = 1; exit_stage = 0; action = 'ENTRY1'

        elif action is None and es == 1 and divergence <= ENTRY2:
            tac_cost += closes[i] * ENTRY2_PCT
            tac_sh   += ENTRY2_PCT
            es = 2; action = 'ENTRY2'

        elif action is None and es == 2 and divergence <= ENTRY3:
            amt = max(0.0, 0.40 - ENTRY1_PCT - ENTRY2_PCT)  # SGOV 40% - 12% - 12% = 16%
            tac_cost += closes[i] * amt; tac_sh += amt
            es = 3; action = 'ENTRY3'

        # 4) 하락 리밸 (평단가 기준, 3차 이후)
        elif action is None and es == 3:
            if rs == 0 and spy_loss <= REBAL_DOWN1_LOSS:
                tac_cost += closes[i] * REBAL_DOWN_PCT
                tac_sh   += REBAL_DOWN_PCT
                rs = 1; action = 'REBAL_DOWN1'
            elif rs == 1 and spy_loss <= REBAL_DOWN2_LOSS:
                tac_cost += closes[i] * REBAL_DOWN_PCT
                tac_sh   += REBAL_DOWN_PCT
                rs = 2; action = 'REBAL_DOWN2'

        # position 값: app.js는 0.0/0.35/0.70/1.0 기대
        # es 단계를 대응 비율로 변환
        pos_map = {0: 0.0, 1: 0.35, 2: 0.70, 3: 1.0}
        cur_pos = pos_map.get(es, 0.0)

        if action:
            trades.append({
                'index': i,
                'type': action,
                'divergence': round(divergence, 2),
                'price': closes[i],
                'position_after': pos_map.get(es, 0.0),
                'avg_entry': round(spy_avg, 2) if spy_avg > 0 else None,
                'spy_loss': round(spy_loss, 2),
            })

        states.append({
            'i': i, 'close': closes[i],
            'predicted': round(predicted, 4),
            'divergence': round(divergence, 4),
            'position': cur_pos,
            'peak': running_peak,
        })

    return trades, states

# ── 포트폴리오 시뮬 ──────────────────────────────────────────
def backtest(states, krw_daily=0.035/252):
    port = [100.0]
    for k in range(1, len(states)):
        prev = states[k-1]; cur = states[k]
        dr = (cur['close']/prev['close']-1) if prev['close'] else 0
        port.append(port[-1]*(1+0.20*prev['position']*dr+0.80*krw_daily))
    return port

def mdd(series):
    peak = series[0]; worst = 0.0
    for v in series:
        if v > peak: peak = v
        worst = max(worst, (peak-v)/peak*100 if peak else 0)
    return worst

# ── 자산별 페이로드 빌드 ─────────────────────────────────────
def build(ticker, cfg):
    print(f"\n[{ticker}] 데이터 수집 중…")
    recs = fetch(cfg['stooq'], cfg['yahoo'])
    if not recs or len(recs) < REG_WIN + 20:
        print(f"  ✗ 데이터 부족 (수집: {len(recs) if recs else 0}개, 필요: {REG_WIN+20}개)")
        return None

    dates  = [r['date']  for r in recs]
    closes = [r['close'] for r in recs]

    trades, states = calc_signals(closes)
    port = backtest(states)
    bh = [100.0]; kd = 0.035/252
    for _ in range(1, len(states)):
        bh.append(bh[-1]*(1+0.80*kd))

    # 현재 신호 (app.js 호환 signal 값)
    cur = states[-1] if states else {}
    pos = cur.get('position', 0.0)
    div = cur.get('divergence')

    sig = 'WAITING'
    if   pos == 0.0  and div is not None and div <= ENTRY1:   sig = 'ENTRY1_READY'
    elif pos == 0.35 and div is not None and div <= ENTRY2:   sig = 'ENTRY2_READY'
    elif pos == 0.70 and div is not None and div <= ENTRY3:   sig = 'ENTRY3_READY'
    elif pos >  0.0  and div is not None and div >= EXIT1_THR: sig = 'EXIT_READY'
    elif pos >  0.0: sig = 'HOLDING'
    # 과열 리밸은 WAITING 유지 (SAFE 상태)

    # 시리즈 패딩 (원본과 동일 구조)
    pad    = REG_WIN
    s_div  = [None]*pad + [s['divergence'] for s in states]
    s_pred = [None]*pad + [s['predicted']  for s in states]
    s_pos  = [0.0]*pad  + [s['position']   for s in states]
    s_peak = [closes[0]]*pad + [s['peak']  for s in states]
    s_port = [100.0]*pad + port
    s_bh   = [100.0]*pad + bh

    n_entries = sum(1 for t in trades if t['type'].startswith('ENTRY'))
    metrics = {
        'total_return': round(port[-1]-100, 2) if port else 0,
        'bh_return':    round(bh[-1]-100, 2) if bh else 0,
        'mdd':          round(mdd(port), 2),
        'trade_count':  n_entries,
    }

    cur_peak = states[-1]['peak'] if states else closes[-1]
    print(f"  ✓ 수익 {metrics['total_return']}% | 신호={sig} | 포지션={pos} | 거래={n_entries}회")
    print(f"    종가={cur.get('close','?')} | 예측가={cur.get('predicted','?')} | 괴리율={div}%")

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
            # ★ 원본 app.js 호환 필드명 유지
            'signal':     sig,
            'position':   pos,
            'divergence': div,
            'close':      cur.get('close'),
            'predicted':  cur.get('predicted'),
            'peak':       cur_peak,
            'date':       dates[-1] if dates else None,
        },
        'params': {
            'reg_window_days': REG_WIN,  # 60M
            'entry1': ENTRY1, 'entry2': ENTRY2, 'entry3': ENTRY3,
            'exit1': EXIT1_THR, 'exit2': EXIT2_THR,
            'rebal_hot': REBAL_THR,
            'rebal_down1': REBAL_DOWN1_LOSS, 'rebal_down2': REBAL_DOWN2_LOSS,
            'regression_method': 'rolling_36m',
            'portfolio': 'SPY40+QQQ20+SGOV40',
        }
    }

# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("최종 전략 v2 — 36M 롤링 회귀 | SPY40+QQQ20+SGOV40")
    print(f"실행: {datetime.utcnow().isoformat()} UTC")
    print("=" * 60)

    out = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name': 'Final Strategy v2 — SPY40+QQQ20+SGOV40 (Rolling 36M Regression)',
            'portfolio': {'spy_pct': 40, 'qqq_pct': 20, 'sgov_pct': 40},
            'dca': {'spy': 200, 'qqq': 100, 'sgov': 200},
            'tiers': [
                {'level':1,'threshold':ENTRY1,'invest_pct':12},
                {'level':2,'threshold':ENTRY2,'invest_pct':12},
                {'level':3,'threshold':ENTRY3,'invest_pct':'sgov잔액(≈16%)'},
            ],
            'exit': [
                {'level':1,'threshold':EXIT1_THR,'sell_pct':50},
                {'level':2,'threshold':EXIT2_THR,'sell_pct':100},
            ],
            'rebal_hot':  {'trigger':REBAL_THR, 'action':'40:20:40복원'},
            'rebal_down': {'trigger1':REBAL_DOWN1_LOSS,'trigger2':REBAL_DOWN2_LOSS,'pct':25},
            'regression_window_days': REG_WIN,
        },
        'assets': {}
    }

    out_path = 'docs/data.json'
    for ticker, cfg in ASSETS.items():
        try:
            result = build(ticker, cfg)
        except Exception as e:
            print(f"  ✗ {ticker} 예외: {e}")
            result = None
        if result:
            out['assets'][ticker] = result
        time.sleep(1)

    if not out['assets']:
        print("\n❌ 수집 실패. 기존 data.json 유지.")
        if os.path.exists(out_path):
            return True
        out['error'] = 'fetch_failed'

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
