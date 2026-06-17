
const ids=[...document.querySelectorAll('input,select')];

function save(){
 ids.forEach(i=>localStorage.setItem(i.id,i.value));
}
function load(){
 ids.forEach(i=>{
   const v=localStorage.getItem(i.id);
   if(v!==null) i.value=v;
 });
}

ids.forEach(i=>i.addEventListener('change',save));

function targets(level){
 if(level==="0") return {sgov:100,spy:0,qqq:0};
 if(level==="1") return {sgov:70,spy:30,qqq:0};
 if(level==="2") return {sgov:40,spy:40,qqq:20};
 return {sgov:0,spy:60,qqq:40};
}

function calculate(){
 save();

 const fx=Number(usdkrw.value)||1400;

 const vals={
  sgov:(+sgovQty.value||0)*(+sgovPrice.value||0),
  spy:(+spyQty.value||0)*(+spyPrice.value||0),
  qqq:(+qqqQty.value||0)*(+qqqPrice.value||0)
 };

 const usdTotal=vals.sgov+vals.spy+vals.qqq;
 const t=targets(level.value);

 const pct={
  sgov:usdTotal?vals.sgov/usdTotal*100:0,
  spy:usdTotal?vals.spy/usdTotal*100:0,
  qqq:usdTotal?vals.qqq/usdTotal*100:0
 };

 function shares(targetPct,currentVal,price){
   const targetVal=usdTotal*targetPct/100;
   return ((targetVal-currentVal)/price).toFixed(1);
 }

 result.innerHTML=`
 <h2>결과</h2>
 <p>달러 자산: $${usdTotal.toFixed(2)}</p>
 <p>달러 자산 원화 환산: ₩${(usdTotal*fx).toLocaleString()}</p>
 <p>원화 현금: ₩${Number(krwCash.value||0).toLocaleString()}</p>
 <hr>
 <p>현재 비중: SGOV ${pct.sgov.toFixed(1)}% / SPY ${pct.spy.toFixed(1)}% / QQQ ${pct.qqq.toFixed(1)}%</p>
 <p>목표 비중: SGOV ${t.sgov}% / SPY ${t.spy}% / QQQ ${t.qqq}%</p>
 <hr>
 <p><b>권장 거래</b></p>
 <p>SGOV ${shares(t.sgov,vals.sgov,(+sgovPrice.value||1))} 주</p>
 <p>SPY ${shares(t.spy,vals.spy,(+spyPrice.value||1))} 주</p>
 <p>QQQ ${shares(t.qqq,vals.qqq,(+qqqPrice.value||1))} 주</p>
 `;
}
load();
calculate();
