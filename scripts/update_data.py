#!/usr/bin/env python3
"""
최종 전략 v7 — 400일 SMA 기반 일간 신호 계산 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
데이터:    일별 수집 → 일별 그대로 사용 (리샘플링 없음)
신호:      SPY 400일 단순이동평균(SMA) 괴리율

포트폴리오: SPY 40% + QQQ 20% + SGOV 40%

월 DCA (3단계 총액 동적, 매월 첫 거래일, 전일 괴리율 기준):
  괴리율 < -3%    →  $750 (SPY $375 + QQQ $188 + SGOV $187)  저평가 확대
  -3% ~ +10%      →  $500 (SPY $200 + QQQ $100 + SGOV $200)  기본
  괴리율 > +10%   →  $300 (SPY $120 + QQQ $60  + SGOV $120)  고평가 축소
  ※ 축소분은 원화 단기자금으로 대기 (강제 아님, 다른 곳에 써도 무방)

진입:  1차(-5%) / 2차(-10%) / 3차(-15%)
리밸:  동적 트리거 18~25% 교차 → 전술+기본 통합 → 40:20:40 → SAFE
       트리거 = 25 - (SMA 60일변화율/10)×(25-18), [18,25] 클리핑
       (변화율 클수록=급등직후 → 낮은 트리거로 빨리 실현 /
        변화율 작을수록=완만한 상승 → 높은 트리거로 오래 보유)
       (쿨다운 60거래일: 재발동까지 최소 60거래일 대기 — SMA 노이즈 과매매 방지)
하락:  평단 -20%/-40% → 외부현금 25%+25%
수수료: 매수 시에만 0.5% (왕복분 선반영), 매도 시 0%
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
백테스트(1994-08~2026-07, 22년, $20K+3단계DCA): $2,481,104 (투입 $191,200)
  CAGR 12.37% | MDD 52.8% | 진입7회 | 리밸17회 | 하락리밸2회 | 수수료 $4,419
  ※ v6(고정$500) 대비 CAGR +0.23%p, MDD -2.5%p, 정규화(동일투입) +$116,140
  ※ 검증: 워크포워드(인/아웃샘플 일관 개선) + 위기제외 평온구간 5/5 승리
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

REBAL_COOLDOWN  = 60      # 거래일. 리밸 후 재발동까지 최소 대기 (노이즈 방지)

# 동적 리밸 트리거 — SMA 60일 변화율 역방향 연동
#   변화율이 클수록(급등 직후) → 트리거 낮춤(빨리 차익실현)
#   변화율이 작을수록(완만한 상승) → 트리거 높임(오래 보유)
#   공식: thr = HI - (mom60/NORM) * (HI-LO), 범위 [LO, HI]로 클리핑
REBAL_MOM_WIN   = 60       # 변화율 측정 기간(거래일)
REBAL_THR_LO    = 18.0     # 트리거 하한
REBAL_THR_HI    = 25.0     # 트리거 상한
REBAL_NORM      = 10.0     # 정규화 상수 (변화율 10%를 만점 기준으로)
# DCA 3단계 (v7): 전일 괴리율 기준 총액+비중 동적 조정
#   괴리율 < -3%   → 총액 $750, 비중 50:25:25 (저평가 확대)
#   -3% ~ +10%     → 총액 $500, 비중 40:20:40 (기본)
#   > +10%         → 총액 $300, 비중 40:20:40 (고평가 축소)
DCA_BULL_THR    = -3.0    # 이하이면 확대
DCA_COOL_THR    = +10.0   # 초과이면 축소
DCA_BULL = {'spy': 375, 'qqq': 188, 'sgov': 187}   # $750
DCA_BASE = {'spy': 200, 'qqq': 100, 'sgov': 200}   # $500
DCA_COOL = {'spy': 120, 'qqq':  60, 'sgov': 120}   # $300

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

def calc_sma_series(closes, win):
    """전체 구간 SMA를 미리 계산 (변화율 조회 성능용)"""
    N = len(closes)
    series = [None] * N
    for i in range(win, N):
        series[i] = sum(closes[i-win:i]) / win
    return series

def sma_change_rate(sma_series, i, win):
    """SMA의 win거래일간 변화율(%). 데이터 부족 시 None"""
    j = i - win
    if j < 0 or sma_series[i] is None or sma_series[j] is None or sma_series[j] == 0:
        return None
    return (sma_series[i] - sma_series[j]) / sma_series[j] * 100.0

def dynamic_rebal_thr(mom):
    """
    동적 리밸 트리거: SMA 60일 변화율 역방향 연동
      변화율 클수록(급등 직후) → 트리거 낮춤(빨리 차익실현)
      변화율 작을수록(완만한 상승) → 트리거 높임(오래 보유)
    mom이 None(데이터 부족)이면 중간값 반환
    """
    if mom is None:
        return (REBAL_THR_LO + REBAL_THR_HI) / 2
    t = REBAL_THR_HI - (mom / REBAL_NORM) * (REBAL_THR_HI - REBAL_THR_LO)
    return max(REBAL_THR_LO, min(REBAL_THR_HI, t))

def calc_signals(dates, closes):
    """
    일별 종가 배열에 대해 400일 SMA 괴리율 기반 신호 계산.
    리밸 트리거는 SMA 60일 변화율에 연동되어 18~25% 범위에서 동적으로 결정됨.
    반환: trades(거래 리스트), states(일별 상태 리스트, SMA 계산 가능 구간부터)
    """
    N = len(closes)
    if N < SMA_WIN + REBAL_MOM_WIN + 30: return [], []

    sma_series = calc_sma_series(closes, SMA_WIN)

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

        s = sma_series[i]
        if not s:
            states.append({'i': i, 'close': closes[i], 'predicted': None,
                           'divergence': None, 'position': pos_map.get(es, 0.0),
                           'peak': running_peak})
            continue

        divergence = (closes[i] - s) / s * 100.0
        mom60 = sma_change_rate(sma_series, i, REBAL_MOM_WIN)
        rebal_thr = dynamic_rebal_thr(mom60)

        spy_avg  = tac_spy_cost / tac_spy_sh if tac_spy_sh > 1e-9 else 0.0
        spy_loss = (closes[i] - spy_avg) / spy_avg * 100 if spy_avg > 0 else 0.0
        action = None

        # ① 완전 리밸: 동적 트리거(18~25%) 교차 (쿨다운 적용 — 과매매 방지)
        can_rebal = (i - last_rebal_i) >= REBAL_COOLDOWN
        if divergence >= rebal_thr and not prev_over and can_rebal:
            tac_spy_sh = 0.0; tac_spy_cost = 0.0
            tac_qqq_sh = 0.0; tac_qqq_cost = 0.0
            es = 0; rs = 0; action = 'REBAL_FULL'
            last_rebal_i = i
        prev_over = (divergence >= rebal_thr)

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
                           'rebal_thr': round(rebal_thr, 2),
                           'sma_mom60': round(mom60, 2) if mom60 is not None else None,
                           'is_dca_day': i in dca_days})
        states.append({'i': i, 'close': closes[i], 'predicted': round(s, 4),
                       'divergence': round(divergence, 4), 'position': cur_pos,
                       'peak': running_peak, 'rebal_thr': round(rebal_thr, 2),
                       'es': es, 'rs': rs,
                       'spy_avg': round(spy_avg, 2) if spy_avg > 0 else None,
                       'spy_loss': round(spy_loss, 2) if spy_avg > 0 else None,
                       'is_dca_day': i in dca_days})

    return trades, states

# ── 현재 신호 판단 ────────────────────────────────────────────
def get_signal(states):
    if not states: return 'NO_SIGNAL'
    last = states[-1]
    pos = last.get('position', 0.0); div = last.get('divergence')
    es = last.get('es', 0); rs = last.get('rs', 0)
    spy_loss = last.get('spy_loss')
    rebal_thr = last.get('rebal_thr', REBAL_THR_HI)
    if div is None: return 'NO_SIGNAL'
    if pos > 0.0 and div >= rebal_thr:      return 'REBAL_FULL_READY'
    if pos == 0.0 and div <= ENTRY1_DIV:    return 'ENTRY1_READY'
    if pos == 0.35 and div <= ENTRY2_DIV:   return 'ENTRY2_READY'
    if pos == 0.70 and div <= ENTRY3_DIV:   return 'ENTRY3_READY'
    if es == 3 and rs == 0 and spy_loss is not None and spy_loss <= REBAL_DOWN1_LOSS:
        return 'REBAL_DOWN1_READY'
    if es == 3 and rs == 1 and spy_loss is not None and spy_loss <= REBAL_DOWN2_LOSS:
        return 'REBAL_DOWN2_READY'
    if pos > 0.0:                            return 'HOLDING'
    return 'WAITING'

SIG_MSG = {
    'WAITING':           '관망 중 — SGOV 보유',
    'ENTRY1_READY':      '⚡ 1차 진입 — SGOV 순자산×12% → SPY:QQQ=2:1',
    'ENTRY2_READY':      '⚡ 2차 진입 — SGOV 순자산×12% → SPY:QQQ=2:1',
    'ENTRY3_READY':      '⚡ 3차 진입 — SGOV 잔액 전부 → SPY:QQQ=2:1',
    'HOLDING':           '전술 포지션 보유 중',
    'REBAL_FULL_READY':  '⚖️ 완전 리밸 — 전술+기본 통합 → 40:20:40 → SAFE',
    'REBAL_DOWN1_READY': '💸 하락 리밸 1차 — 외부현금 순자산×25% → SPY:QQQ=2:1',
    'REBAL_DOWN2_READY': '💸 하락 리밸 2차 — 외부현금 순자산×25% → SPY:QQQ=2:1',
    'NO_SIGNAL':         '신호 없음 (데이터 부족)',
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
    pos       = last.get('position', 0.0)
    div       = last.get('divergence')
    pred      = last.get('predicted')
    peak      = last.get('peak')
    rebal_thr = last.get('rebal_thr')
    es        = last.get('es', 0)
    rs        = last.get('rs', 0)
    spy_avg   = last.get('spy_avg')
    spy_loss  = last.get('spy_loss')

    latest_close = closes[-1]
    latest_date  = dates[-1]

    pad    = SMA_WIN
    s_div  = [None]         * pad + [s['divergence'] for s in states]
    s_pred = [None]         * pad + [s['predicted']  for s in states]
    s_pos  = [0.0]          * pad + [s['position']   for s in states]
    s_peak = [closes[0]]    * pad + [s['peak']       for s in states]
    s_rthr = [None]         * pad + [s['rebal_thr']  for s in states]

    n_entry = sum(1 for t in trades if t['type'].startswith('ENTRY'))
    n_rebal = sum(1 for t in trades if 'REBAL' in t['type'])

    print(f"  ✓ 신호={sig} | 400일SMA괴리율={div}% | 예측가(SMA)={pred}")
    print(f"    동적리밸트리거={rebal_thr}% | 현재가={latest_close} ({latest_date})")
    print(f"    진입{n_entry}회 | 리밸{n_rebal}회 | es={es} rs={rs} spy_loss={spy_loss}")

    return {
        'ticker': ticker, 'name': cfg['name'],

        # 일별 시리즈 (SMA/괴리율 차트, 400일 워밍업 이후부터 유효)
        'dates':      dates,
        'closes':     closes,
        'divergence': s_div,
        'predicted':  s_pred,
        'position':   s_pos,
        'peak':       s_peak,
        'rebal_thr':  s_rthr,   # 동적 리밸 트리거값 시리즈 (18~25%)

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
            'rebal_thr':    rebal_thr,     # 현재 시점 동적 리밸 트리거(18~25%)
            'dca_tier':     ('bull' if div is not None and div < DCA_BULL_THR else
                             'cool' if div is not None and div > DCA_COOL_THR else 'base'),
            'dca_amounts':  (DCA_BULL if div is not None and div < DCA_BULL_THR else
                             DCA_COOL if div is not None and div > DCA_COOL_THR else DCA_BASE),
            'es':           es,            # 진입 단계 (0~3)
            'rs':           rs,            # 하락리밸 단계 (0~2)
            'spy_avg':      spy_avg,       # 전술 포지션 SPY 평단가
            'spy_loss':     spy_loss,      # 평단 대비 SPY 손익률(%)
            'date':         latest_date,
        },

        'params': {
            'portfolio':     'SPY40+QQQ20+SGOV40',
            'signal':        'SPY 400일 단순이동평균(SMA) 괴리율 (일별)',
            'data_freq':     'daily (no resample)',
            'sma_win':       SMA_WIN,
            'entry_div':     [ENTRY1_DIV, ENTRY2_DIV, ENTRY3_DIV],
            'entry_pct':     [ENTRY1_PCT*100, ENTRY2_PCT*100, '잔액전부(≈16%)'],
            'rebal_full':    {'mode':'dynamic', 'lo':REBAL_THR_LO, 'hi':REBAL_THR_HI,
                              'mom_win':REBAL_MOM_WIN, 'norm':REBAL_NORM},
            'rebal_cooldown':f'{REBAL_COOLDOWN}거래일',
            'rebal_down':    [REBAL_DOWN1_LOSS, REBAL_DOWN2_LOSS, REBAL_DOWN_PCT*100],
            'dca_mode':      '3-tier (v7)',
            'dca_bull':      {'thr':f'괴리율<{DCA_BULL_THR}%', 'total':750, **DCA_BULL},
            'dca_base':      {'thr':f'{DCA_BULL_THR}%~+{DCA_COOL_THR}%', 'total':500, **DCA_BASE},
            'dca_cool':      {'thr':f'괴리율>+{DCA_COOL_THR}%', 'total':300, **DCA_COOL},
        },
    }

# ── 메인 ─────────────────────────────────────────────────────
def main():
    print("=" * 65)
    print("최종 전략 v7 — 400일 SMA (일별) | 신호=SPY 400SMA 괴리율")
    print(f"실행: {datetime.utcnow().isoformat()} UTC")
    print(f"SMA={SMA_WIN}거래일 | 리밸=동적{REBAL_THR_LO}~{REBAL_THR_HI}%"
          f"(SMA{REBAL_MOM_WIN}일변화율연동,쿨다운{REBAL_COOLDOWN}거래일) | DCA 3단계($300/500/750)")
    print("=" * 65)

    output = {
        'generated_at': datetime.utcnow().isoformat() + 'Z',
        'strategy': {
            'name':    'Final v7 — SPY40+QQQ20+SGOV40 | 400-Day SMA + Dynamic Rebal + 3-Tier DCA (Daily)',
            'version': 'v7',
            'note':    '일별 데이터 그대로 사용. 400일 SMA 괴리율 기준, '
                       '리밸 트리거는 SMA 60일 변화율에 역방향 연동(18~25%), '
                       'DCA는 괴리율 3단계 총액 동적($300/$500/$750)',
            'portfolio': {'spy': W_SPY, 'qqq': W_QQQ, 'sgov': W_SGOV},
            'dca': {
                'mode': '3-tier',
                'bull': {'thr': f'괴리율 < {DCA_BULL_THR}%', 'total': 750, 'alloc': DCA_BULL},
                'base': {'thr': f'{DCA_BULL_THR}% ~ +{DCA_COOL_THR}%', 'total': 500, 'alloc': DCA_BASE},
                'cool': {'thr': f'괴리율 > +{DCA_COOL_THR}%', 'total': 300, 'alloc': DCA_COOL},
                'note': '전일 괴리율 기준, 매월 첫 거래일 집행. 축소분은 원화 단기자금으로 대기',
            },
            'entry': [
                {'level':1,'div':ENTRY1_DIV,'pct':f'{ENTRY1_PCT*100:.0f}%'},
                {'level':2,'div':ENTRY2_DIV,'pct':f'{ENTRY2_PCT*100:.0f}%'},
                {'level':3,'div':ENTRY3_DIV,'pct':'SGOV잔액전부(≈16%)'},
            ],
            'rebal_full': {
                'mode': 'dynamic',
                'range': f'{REBAL_THR_LO}%~{REBAL_THR_HI}%',
                'formula': f'thr = {REBAL_THR_HI} - (SMA{REBAL_MOM_WIN}일변화율/{REBAL_NORM})×({REBAL_THR_HI}-{REBAL_THR_LO}), 범위클리핑',
                'logic': '변화율 클수록(급등직후)→트리거낮춤(빨리실현) / 변화율작을수록(완만한상승)→트리거높임(오래보유)',
                'cooldown': f'{REBAL_COOLDOWN}거래일',
                'action': '전술+기본통합→40:20:40→SAFE',
            },
            'rebal_down': {'t1':REBAL_DOWN1_LOSS,'t2':REBAL_DOWN2_LOSS,
                           'pct':f'{REBAL_DOWN_PCT*100:.0f}%'},
            'backtest': {'final':2481104,'invested':191200,'cagr':12.37,'mdd':52.8,
                        'normalized_final':2744527,
                        'commission':4419,
                        'period':'1994-08~2026-07 (22년)',
                        'note':'수수료 매수시에만 0.5%. DCA 3단계: v6 대비 CAGR +0.23%p, MDD -2.5%p, '
                               '정규화(동일투입) +$116,140. 워크포워드·위기제외 5/5구간 검증 통과'},
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
