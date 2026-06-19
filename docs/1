/* ============================================================
   변곡記 — app.js   (순수 Canvas, 외부 의존성 없음)
   ============================================================ */
'use strict';

const C = {
  ink:'#1b2430',soft:'#44505f',navy:'#1d3557',
  brass:'#b8893f',green:'#3d6b4f',red:'#a13d2b',
  paper:'#f3efe4',dim:'#e9e2d2',hair:'rgba(27,36,48,0.11)',
};

const SIGNALS = {
  ENTRY1_READY:{label:'1차 진입 신호',cls:'sig-entry',
    desc:'괴리율이 −5% 이하입니다. 달러자산의 35%(포트폴리오 7%)를 SPY/QQQ로 전환하세요.'},
  ENTRY2_READY:{label:'2차 추가 신호',cls:'sig-entry',
    desc:'괴리율이 −10% 이하입니다. 달러자산 비중을 70%(포트폴리오 14%)로 높이세요.'},
  ENTRY3_READY:{label:'3차 풀포지션 신호',cls:'sig-entry',
    desc:'괴리율이 −20% 이하입니다. 달러자산 100%(포트폴리오 20%)로 풀 포지션을 구성하세요.'},
  EXIT_READY:{label:'청산 신호',cls:'sig-exit',
    desc:'가격이 회귀선 위로 복귀했습니다(괴리율 ≥ 0%). 전량 청산 후 SGOV/CMA로 돌아오세요.'},
  HOLDING:{label:'보유 유지',cls:'sig-hold',
    desc:'현재 포지션을 유지합니다. 다음 단계 진입 또는 청산 조건에 미달입니다.'},
  WAITING:{label:'대기 — SGOV/CMA',cls:'sig-wait',
    desc:'진입 조건 미충족. 원화 80% CMA + 달러 20% SGOV 상태를 유지합니다.'},
};

let state={data:null,ticker:'SPY'};

const f2=(v,d=2)=>(v===null||v===undefined||isNaN(v))?'—':Number(v).toLocaleString('en-US',{minimumFractionDigits:0,maximumFractionDigits:d});
const fp=(v,sign=true)=>{if(v===null||v===undefined||isNaN(v))return'—';return(sign&&v>=0?'+':'')+Number(v).toFixed(2)+'%';};

async function load(){
  const r=await fetch('data.json?t='+Date.now(),{cache:'no-store'});
  if(!r.ok)throw new Error('data.json load failed');
  return r.json();
}

function setBadge(gen,placeholder){
  const dot=document.getElementById('dot'),lb=document.getElementById('updAt');
  if(!gen){lb.textContent='갱신 시각 불명';dot.classList.add('stale');return;}
  const dt=new Date(gen);
  const kst=new Date(dt.getTime()+9*3600000);
  const stamp=kst.toISOString().slice(0,16).replace('T',' ')+' KST';
  lb.textContent=placeholder?'샘플 데이터 · '+stamp:'마지막 갱신: '+stamp;
  if(placeholder||(Date.now()-dt)/36e5>30)dot.classList.add('stale');
}

function renderGauge(asset){
  const cur=asset.current;
  const cp=SIGNALS[cur.signal]||SIGNALS.WAITING;
  document.getElementById('aName').textContent=asset.name.replace(' [placeholder]','');
  const sig=document.getElementById('sigTxt');
  sig.textContent=cp.label;sig.className='gauge-sig '+cp.cls;
  document.getElementById('sigDesc').textContent=cp.desc;
  document.getElementById('sClose').textContent=f2(cur.close);
  document.getElementById('sPred').textContent=f2(cur.predicted);
  const dd=document.getElementById('sDiv');
  dd.textContent=fp(cur.divergence);
  dd.className=(cur.divergence??0)<0?'neg':'pos';
  document.getElementById('peakVal').textContent=f2(cur.peak);
  renderDepth(cur.divergence);
  renderLadder(cur);
}

function renderDepth(div){
  const svg=document.getElementById('depthSvg');
  svg.innerHTML='';
  const W=110,H=240,CX=55,MIN=-25,MAX=5;
  const ns='http://www.w3.org/2000/svg';
  const el=(tag,attrs)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,v));return e;};
  const toY=v=>((MAX-Math.max(MIN,Math.min(MAX,v)))/(MAX-MIN))*H;

  [{top:-20,bot:-25,fill:'rgba(61,107,79,0.15)'},{top:-10,bot:-20,fill:'rgba(184,137,63,0.13)'},{top:-5,bot:-10,fill:'rgba(184,137,63,0.08)'}]
    .forEach(z=>{svg.appendChild(el('rect',{x:CX-5,y:toY(z.top),width:10,height:Math.abs(toY(z.bot)-toY(z.top)),fill:z.fill}));});

  svg.appendChild(el('line',{x1:CX,y1:0,x2:CX,y2:H,stroke:C.ink,'stroke-width':1.5}));

  [{v:5,l:'+5%'},{v:0,l:'0% 청산'},{v:-5,l:'-5% 1차'},{v:-10,l:'-10% 2차'},{v:-20,l:'-20% 3차'}].forEach(z=>{
    const y=toY(z.v);
    svg.appendChild(el('line',{x1:CX-6,y1:y,x2:CX+6,y2:y,stroke:C.soft,'stroke-width':1}));
    const t=el('text',{x:CX+10,y:y+4,'font-size':'9',fill:C.soft,'font-family':'monospace'});
    t.textContent=z.l;svg.appendChild(t);
  });

  if(div!==null&&div!==undefined){
    const y=toY(div);
    const mc=div<=-20?C.green:div<=-5?C.brass:div>=0?C.red:C.soft;
    svg.appendChild(el('circle',{cx:CX,cy:y,r:9,fill:mc,stroke:C.ink,'stroke-width':1.5}));
    const t=el('text',{x:CX,y:y+4,'text-anchor':'middle','font-size':'8','font-weight':'700',fill:C.paper,'font-family':'monospace'});
    t.textContent=div.toFixed(0);svg.appendChild(t);
  }
}

function renderLadder(cur){
  document.querySelectorAll('.rung').forEach(r=>r.classList.remove('active','done'));
  const pos=cur.position,sig=cur.signal;
  if(sig==='EXIT_READY'){document.querySelector('.rung[data-tier="exit"]').classList.add('active');return;}
  const activeTier=pos===1.0?'3':pos===0.70?(sig==='ENTRY3_READY'?'3':'2'):pos===0.35?(sig==='ENTRY2_READY'?'2':'1'):(sig==='ENTRY1_READY'?'1':null);
  if(!activeTier)return;
  ['1','2','3'].forEach((t,i)=>{
    const e=document.querySelector('.rung[data-tier="'+t+'"]');
    if(!e)return;
    const ai=['1','2','3'].indexOf(activeTier);
    if(i<ai)e.classList.add('done');
    if(i===ai)e.classList.add('active');
  });
}

function resizeCanvas(id){
  const cv=document.getElementById(id);
  cv.width=cv.parentElement.offsetWidth||800;
  cv.height=parseInt(cv.getAttribute('height')||280);
  return cv;
}
function getCtx(id){return document.getElementById(id).getContext('2d');}

function makeScale(arrs,pad=0.05){
  const vals=arrs.flat().filter(v=>v!==null&&v!==undefined&&!isNaN(v));
  let lo=Math.min(...vals),hi=Math.max(...vals);
  const m=(hi-lo)*pad;
  return{lo:lo-m,hi:hi+m};
}

function toXY(vals,sc,W,H,P){
  return vals.map((v,i)=>[
    P.l+(i/(vals.length-1||1))*(W-P.l-P.r),
    v===null||v===undefined?null:P.t+(1-(v-sc.lo)/(sc.hi-sc.lo))*(H-P.t-P.b)
  ]);
}

function drawAxes(ctx,W,H,P,sc,labels,nY=6,pctY=false){
  ctx.save();
  for(let i=0;i<nY;i++){
    const v=sc.lo+(sc.hi-sc.lo)*i/(nY-1);
    const y=P.t+(1-(v-sc.lo)/(sc.hi-sc.lo))*(H-P.t-P.b);
    ctx.strokeStyle=C.hair;ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(P.l,y);ctx.lineTo(W-P.r,y);ctx.stroke();
    ctx.fillStyle=C.soft;ctx.font='10px monospace';ctx.textAlign='right';
    ctx.fillText(pctY?v.toFixed(1)+'%':v.toLocaleString('en-US',{maximumFractionDigits:0}),P.l-4,y+3);
  }
  if(pctY&&sc.lo<0&&sc.hi>0){
    const y0=P.t+(1-(0-sc.lo)/(sc.hi-sc.lo))*(H-P.t-P.b);
    ctx.strokeStyle=C.ink;ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(P.l,y0);ctx.lineTo(W-P.r,y0);ctx.stroke();
  }
  if(labels&&labels.length){
    ctx.fillStyle=C.soft;ctx.font='10px monospace';ctx.textAlign='center';
    for(let i=0;i<=5;i++){
      const idx=Math.floor(i*(labels.length-1)/5);
      const x=P.l+(idx/(labels.length-1||1))*(W-P.l-P.r);
      ctx.fillText(labels[idx]||'',x,H-P.b+14);
    }
  }
  ctx.restore();
}

function renderPriceChart(asset){
  const cv=resizeCanvas('priceC');
  const ctx=getCtx('priceC');
  const W=cv.width,H=cv.height,P={l:70,r:14,t:14,b:28};
  const N=asset.dates.length,S=Math.max(0,N-360);
  const dates=asset.dates.slice(S),closes=asset.closes.slice(S),pred=asset.predicted.slice(S);
  ctx.clearRect(0,0,W,H);
  const sc=makeScale([closes,pred.filter(Boolean)]);
  const cP=toXY(closes,sc,W,H,P),pP=toXY(pred,sc,W,H,P);
  drawAxes(ctx,W,H,P,sc,dates);

  // price line
  ctx.save();ctx.strokeStyle=C.navy;ctx.lineWidth=1.8;ctx.beginPath();
  cP.forEach(([x,y],i)=>{if(y===null)return;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.stroke();ctx.restore();
  // regression dashed
  ctx.save();ctx.strokeStyle=C.brass;ctx.lineWidth=1.4;ctx.setLineDash([5,4]);ctx.beginPath();
  pP.forEach(([x,y],i)=>{if(y===null)return;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.stroke();ctx.restore();
  // trade markers
  asset.trades.filter(t=>t.index>=S).forEach(t=>{
    const ri=t.index-S;if(ri<0||ri>=cP.length)return;
    const[x,y]=cP[ri];if(y===null)return;
    ctx.save();ctx.fillStyle=t.type.startsWith('ENTRY')?C.green:C.red;
    if(t.type.startsWith('ENTRY')){ctx.beginPath();ctx.moveTo(x,y-8);ctx.lineTo(x-5,y+1);ctx.lineTo(x+5,y+1);ctx.closePath();ctx.fill();}
    else{ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fill();}
    ctx.restore();
  });
}

function renderDivChart(asset){
  const cv=resizeCanvas('divC');
  const ctx=getCtx('divC');
  const W=cv.width,H=cv.height,P={l:50,r:14,t:10,b:26};
  const N=asset.dates.length,S=Math.max(0,N-360);
  const dates=asset.dates.slice(S),div=asset.divergence.slice(S);
  ctx.clearRect(0,0,W,H);
  const sc=makeScale([div.filter(Boolean)],0.08);
  const pts=toXY(div,sc,W,H,P);

  // zone fills
  [{lo:-100,hi:-20,fill:'rgba(61,107,79,0.12)'},{lo:-20,hi:-10,fill:'rgba(184,137,63,0.10)'},{lo:-10,hi:-5,fill:'rgba(184,137,63,0.06)'}]
    .forEach(z=>{
      const yT=P.t+(1-(Math.min(z.hi,sc.hi)-sc.lo)/(sc.hi-sc.lo))*(H-P.t-P.b);
      const yB=P.t+(1-(Math.max(z.lo,sc.lo)-sc.lo)/(sc.hi-sc.lo))*(H-P.t-P.b);
      ctx.fillStyle=z.fill;ctx.fillRect(P.l,yT,W-P.l-P.r,yB-yT);
    });

  drawAxes(ctx,W,H,P,sc,dates,5,true);
  ctx.save();ctx.strokeStyle='#7a5c2e';ctx.lineWidth=1.4;ctx.beginPath();
  pts.forEach(([x,y],i)=>{if(y===null)return;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
  ctx.stroke();ctx.restore();
}

function renderPortChart(asset){
  const cv=resizeCanvas('portC');
  const ctx=getCtx('portC');
  const W=cv.width,H=cv.height,P={l:60,r:14,t:14,b:28};
  const port=asset.port_series,bh=asset.bh_series;
  const dates=asset.dates.slice(1);
  const N=port.length,step=Math.max(1,Math.floor(N/500));
  const idxs=Array.from({length:Math.ceil(N/step)},(_,i)=>Math.min(i*step,N-1));
  const sp=idxs.map(i=>port[i]),sb=idxs.map(i=>bh[i]),sd=idxs.map(i=>dates[i]||'');
  ctx.clearRect(0,0,W,H);
  const sc=makeScale([sp,sb]);
  const pP=toXY(sp,sc,W,H,P),bP=toXY(sb,sc,W,H,P);
  drawAxes(ctx,W,H,P,sc,sd);

  ctx.save();ctx.strokeStyle='#9a9486';ctx.lineWidth=1.3;ctx.setLineDash([4,4]);ctx.beginPath();
  bP.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();ctx.restore();

  const botY=P.t+(H-P.t-P.b);
  ctx.save();ctx.fillStyle='rgba(61,107,79,0.07)';ctx.beginPath();
  ctx.moveTo(pP[0][0],botY);pP.forEach(([x,y])=>ctx.lineTo(x,y));ctx.lineTo(pP[pP.length-1][0],botY);ctx.closePath();ctx.fill();ctx.restore();

  ctx.save();ctx.strokeStyle=C.green;ctx.lineWidth=2;ctx.beginPath();
  pP.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.stroke();ctx.restore();
}

const TAG={ENTRY1:['1차','t-e1'],ENTRY2:['2차','t-e2'],ENTRY3:['3차','t-e3'],EXIT:['청산','t-ex'],STOP_LOSS:['손절','t-sl']};
function renderLog(asset){
  const body=document.getElementById('logB');
  const trades=(asset.latest_trades||[]).slice().reverse();
  if(!trades.length){body.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--ink-soft);padding:20px">거래 기록 없음</td></tr>';return;}
  body.innerHTML=trades.map(t=>{
    const date=asset.dates[t.index]||'—';
    const[lbl,cls]=TAG[t.type]||[t.type,'t-e1'];
    return`<tr><td>${date}</td><td><span class="tag ${cls}">${lbl}</span></td><td>${fp(t.divergence)}</td><td>${f2(t.price)}</td><td>${(t.position_after*20).toFixed(0)}%</td></tr>`;
  }).join('');
}

function renderMNote(asset){
  const m=asset.metrics;
  document.getElementById('mNote').textContent=`전략 ${fp(m.total_return)} · B&H ${fp(m.bh_return)} · MDD ${m.mdd.toFixed(1)}% · 거래 ${m.trade_count}회`;
}

function renderAsset(ticker){
  const a=state.data.assets[ticker];if(!a)return;
  state.ticker=ticker;
  renderGauge(a);renderPriceChart(a);renderDivChart(a);renderPortChart(a);renderLog(a);renderMNote(a);
}

function initTabs(){
  document.querySelectorAll('.flag').forEach(btn=>{
    btn.addEventListener('click',()=>{
      document.querySelectorAll('.flag').forEach(b=>b.classList.remove('active'));
      btn.classList.add('active');renderAsset(btn.dataset.t);
    });
  });
}

async function boot(){
  try{
    const data=await load();state.data=data;
    setBadge(data.generated_at,!!data.placeholder);
    document.getElementById('loading').style.display='none';
    document.getElementById('app').style.display='block';
    initTabs();
    renderAsset(Object.keys(data.assets)[0]||'SPY');
    const rl=document.getElementById('repoA');
    if(window.location.hostname.includes('github.io')){
      const user=window.location.hostname.split('.')[0];
      const repo=window.location.pathname.split('/')[1];
      rl.href=`https://github.com/${user}/${repo}`;
    }
  }catch(e){
    console.error(e);
    document.getElementById('loading').textContent='데이터를 불러오지 못했습니다. 잠시 후 새로고침하세요.';
  }
}

let rt;window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(()=>{if(state.data)renderAsset(state.ticker);},200);});
boot();
