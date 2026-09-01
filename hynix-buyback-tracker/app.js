const REFRESH_MS = 30000;
const REQUEST_TIMEOUT_MS = 15000;
const RETRY_DELAY_MS = 1500;
const ERROR_AFTER_FAILURES = 3;
const PUBLIC_API_URL = 'https://script.google.com/macros/s/AKfycbznUeW68apO8MM2AEX_T_PZ2FfwrPGfojUIgDSDRXz-YRIzTbGbOUhb30BzUxue90qHQA/exec';

let refreshRunning = false;
let consecutiveFailures = 0;
let hasLiveData = false;

const $ = id => document.getElementById(id);
const fmtInt = v => Number.isFinite(Number(v)) ? Math.round(Number(v)).toLocaleString('ko-KR') : '-';
const fmtQty = v => Number.isFinite(Number(v)) ? `${fmtInt(v)}주` : '-';
const fmtPrice = v => Number.isFinite(Number(v)) && Number(v) > 0 ? `${fmtInt(v)}원` : '-';
const pctValue = v => {
  if(v===null || v===undefined) return null;
  const text=String(v).trim().replace(/,/g,'');
  if(!text) return null;
  const isPercent=text.endsWith('%');
  const n=Number(isPercent?text.slice(0,-1):text);
  if(!Number.isFinite(n)) return null;
  if(isPercent) return n;
  return Math.abs(n) <= 1.5 ? n * 100 : n;
};
const fmtPct = v => { const n=pctValue(v); return n===null?'-':`${n.toFixed(1)}%`; };
const fmtPctSigned = v => { const n=pctValue(v); return n===null?'-':`${n>0?'+':''}${n.toFixed(2)}%`; };
const fmtX = v => Number.isFinite(Number(v)) ? `${Number(v).toFixed(2)}x` : '-';
const safeTime = v => String(v || '').slice(0,5) || '-';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function signalMeta(signal){
  const s=String(signal||'WAIT').toUpperCase();
  if(s==='GREEN') return {cls:'green',desc:'남은 매입 여력이 충분한 우호 구간',title:'자사주 하방지원 강',chip:'눌림 대기'};
  if(s==='YELLOW') return {cls:'yellow',desc:'매입은 진행 중이지만 추격은 주의할 구간',title:'자사주 지원 진행 구간',chip:'추격 주의'};
  if(s==='RED') return {cls:'red',desc:'남은 물량이 적거나 소진된 구간',title:'자사주 지원 소진 경계',chip:'관망 우선'};
  return {cls:'neutral',desc:'SK증권 상위 5위 포착을 기다리는 구간',title:'SK증권 포착 대기',chip:'대기'};
}

function setNeedle(rate){
  const r=Math.max(0,Math.min(1,Number(rate)||0));
  const angle=Math.PI-(Math.PI*r);
  const cx=210,cy=205,len=147;
  const x=cx+Math.cos(angle)*len;
  const y=cy-Math.sin(angle)*len;
  $('needle').setAttribute('x2',x.toFixed(1));
  $('needle').setAttribute('y2',y.toFixed(1));
}

function setDecision(data,meta){
  const raw=String(data.decision||'').trim();
  let title=meta.title, desc=meta.desc, chip=meta.chip;
  if(raw){
    const parts=raw.split('/').map(s=>s.trim()).filter(Boolean);
    if(parts[0]) title=parts[0];
    if(parts[1]) desc=parts[1];
  }
  if(String(data.signal).toUpperCase()==='RED') desc='신청수량 소진 또는 장 후반 구간입니다.';
  $('decisionTitle').textContent=title;
  $('decisionDesc').textContent=desc;
  $('decisionChip').textContent=chip;
  $('decisionBox').className=`box ${meta.cls}`;
}

function renderChart(rows, applicationQty){
  const svg=$('trend');
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  const data=Array.isArray(rows)?rows:[];
  const W=1100,H=280,L=54,R=24,T=18,B=36;
  const target=Math.max(1,Number(applicationQty)||1);
  const NS='http://www.w3.org/2000/svg';
  const add=(tag,a)=>{const n=document.createElementNS(NS,tag);Object.entries(a).forEach(([k,v])=>n.setAttribute(k,v));svg.appendChild(n);return n;};
  if(!data.length){const t=add('text',{x:W/2,y:H/2,fill:'#98a2b3','font-size':16,'text-anchor':'middle'});t.textContent='장중 데이터 대기';return;}
  const x=i=>data.length===1?(L+(W-R))/2:L+(W-L-R)*i/(data.length-1);
  const y=v=>T+(H-T-B)*(1-Math.max(0,Math.min(target,Number(v)||0))/target);
  [0,.25,.5,.75,1].forEach(r=>{
    const yy=T+(H-T-B)*(1-r); add('line',{x1:L,y1:yy,x2:W-R,y2:yy,stroke:'#e7eaf0'});
    const t=add('text',{x:8,y:yy+4,fill:'#98a2b3','font-size':11}); t.textContent=`${Math.round(target*r/1000)}k`;
  });
  add('line',{x1:L,y1:y(target),x2:W-R,y2:y(target),stroke:'#c5cad3','stroke-width':2,'stroke-dasharray':'6 6'});
  let last=null; const pts=[];
  data.forEach((row,i)=>{ if(row && row.skQty!==null && row.skQty!=='' && Number.isFinite(Number(row.skQty))) last=Number(row.skQty); if(last!==null) pts.push([x(i),y(last),i]); });
  if(pts.length){let d=`M ${pts[0][0]} ${pts[0][1]}`;for(let j=1;j<pts.length;j++)d+=` L ${pts[j][0]} ${pts[j][1]}`;add('path',{d,fill:'none',stroke:'#2563eb','stroke-width':4,'stroke-linecap':'round','stroke-linejoin':'round'});pts.forEach((q,j)=>add('circle',{cx:q[0],cy:q[1],r:j===pts.length-1?5:3,fill:'#2563eb',stroke:'#fff','stroke-width':2}));}
  const step=Math.max(1,Math.floor(data.length/5));
  data.forEach((row,i)=>{if(i===0||i===data.length-1||i%step===0){const t=add('text',{x:x(i),y:H-12,fill:'#98a2b3','font-size':11,'text-anchor':'middle'});t.textContent=String(row.time||'').slice(0,5);}});
}

function render(data){
  if(!data || data.ok!==true) throw new Error((data&&data.error)||'invalid_payload');
  const meta=signalMeta(data.signal);
  const signal=String(data.signal||'WAIT').toUpperCase();
  $('signal').textContent=signal;
  $('signal').className=`state ${meta.cls==='neutral'?'wait':meta.cls}`;
  $('signalDesc').textContent=meta.desc;
  const rank=Number.isFinite(Number(data.rankCurrent))?Number(data.rankCurrent):null;
  $('rankBadge').textContent=rank?`SK증권 매수 ${rank}위`:'SK증권 상위5 미포착';
  $('rankBadge').className=`rank ${rank?'':'neutral'}`;
  $('rankText').textContent=rank?`현재 매수순위 ${rank}위`:'현재 상위5 미포착';
  $('currentRank').textContent=rank?`매수 ${rank}위`:'상위5 미포착';
  $('remainingRate').textContent=fmtPct(data.remainingRate);
  $('remainingRateSmall').textContent=`잔여율 ${fmtPct(data.remainingRate)}`;
  setNeedle(data.remainingRate);
  const progress=Math.max(0,Math.min(1,Number(data.progress)||0));
  $('progressText').textContent=fmtPct(progress);
  $('progressBar').style.width=`${(progress*100).toFixed(1)}%`;
  $('price').textContent=fmtPrice(data.price);
  const priceNum=Number(data.price);
  const prevCloseNum=Number(data.prevClose);
  let cp=null;
  if(Number.isFinite(priceNum) && Number.isFinite(prevCloseNum) && prevCloseNum>0){
    cp=(priceNum/prevCloseNum-1)*100;
  }else{
    cp=pctValue(data.changePct);
  }
  $('changePct').textContent=cp===null?'-':`${cp>0?'+':''}${cp.toFixed(2)}%`;
  $('changePct').className=`chg ${cp===null||cp===0?'flat':cp>0?'up':'down'}`;
  $('prevClose').textContent=`전일 종가 ${fmtPrice(data.prevClose)}`;
  $('applicationQty').textContent=fmtQty(data.applicationQty);
  $('skQty').textContent=fmtQty(data.skQty);
  $('currentSkQty').textContent=fmtQty(data.skQty);
  $('remainingQty').textContent=fmtQty(data.remainingQty);
  $('depletionTime').textContent=safeTime(data.depletionTime);
  $('marketVolume').textContent=fmtQty(data.marketVolume);
  $('marketShare').textContent=fmtPct(data.marketShare);
  $('avgSpeed').textContent=Number.isFinite(Number(data.avgSpeed))?Number(data.avgSpeed).toLocaleString('ko-KR',{maximumFractionDigits:3}):'-';
  $('recentSpeed').textContent=fmtInt(data.recentSpeed);
  $('paceMultiple').textContent=fmtX(data.paceMultiple);
  $('acceleration').textContent=fmtX(data.acceleration);
  $('neededSpeed').textContent=fmtInt(data.neededSpeed);
  $('elapsedMin').textContent=Number.isFinite(Number(data.elapsedMin))?`${fmtInt(data.elapsedMin)}분`:'-';
  const apiTime=safeTime(data.apiTime);
  const date=String(data.date||'').replace(/\./g,'-');
  $('liveState').textContent='● LIVE'; $('liveState').className='live';
  $('liveTime').textContent=`${date} ${apiTime} 기준 · 30초 자동 갱신`;
  $('updatePanel').className='update-panel';
  $('updateTime').textContent=apiTime;
  $('updateDate').textContent=`${date} · 30초 자동 갱신`;
  $('firstSeen').textContent=data.firstSeenTime?`${safeTime(data.firstSeenTime)} · ${fmtQty(data.firstSeenQty)}`:'아직 미포착';
  const crossed=String(data.crossingStatus||'').includes('통과') && !String(data.crossingStatus||'').includes('미통과');
  $('crossing').textContent=crossed?`${safeTime(data.crossingTime)} · 통과`:'미통과';
  setDecision(data,meta);
  renderChart(data.timeseries,data.applicationQty);
}

function failureReason(error){
  const reason=String(error&&error.message?error.message:error||'unknown_error');
  if(reason==='jsonp_timeout') return 'jsonp_timeout';
  if(reason==='jsonp_load_error') return 'jsonp_load_error';
  return reason.slice(0,80);
}

function recordFailure(error,attempt){
  const reason=failureReason(error);
  const entry={time:new Date().toISOString(),attempt,reason};
  console.warn('[hynix-live-request-failure]',entry);
  if(!Array.isArray(window.__hynixConnectionFailures)) window.__hynixConnectionFailures=[];
  window.__hynixConnectionFailures.push(entry);
  if(window.__hynixConnectionFailures.length>20) window.__hynixConnectionFailures.shift();
  return reason;
}

function showConnectionFailure(error){
  const reason=failureReason(error);
  const isHardError=consecutiveFailures>=ERROR_AFTER_FAILURES;
  $('liveState').textContent=isHardError?'● 연결 오류':'● 연결 지연';
  $('liveState').className=`live ${isHardError?'error':'pending'}`;
  $('updatePanel').className=`update-panel ${isHardError?'error':'pending'}`;

  if(hasLiveData){
    $('liveTime').textContent=isHardError
      ? `최근 정상 데이터 유지 · ${consecutiveFailures}회 연속 실패 · ${reason}`
      : `최근 정상 데이터 유지 · 재연결 중 (${consecutiveFailures}/${ERROR_AFTER_FAILURES})`;
    return;
  }

  $('liveTime').textContent=isHardError?'실시간 API 연결 실패':'실시간 API 응답 지연 · 재연결 중';
  $('updateTime').textContent=isHardError?'연결 오류':'연결 지연';
  $('updateDate').textContent=reason;
}

function requestJsonp(){
  return new Promise((resolve,reject)=>{
    const callbackName=`__hynixLive_${Date.now()}_${Math.random().toString(36).slice(2)}`;
    const script=document.createElement('script');
    let settled=false;
    let timer=null;

    const cleanup=()=>{
      if(timer) clearTimeout(timer);
      try{ delete window[callbackName]; }catch{}
      script.remove();
    };
    const finish=(handler,value)=>{
      if(settled) return;
      settled=true;
      cleanup();
      handler(value);
    };

    window[callbackName]=(data)=>finish(resolve,data);
    script.onerror=()=>finish(reject,new Error('jsonp_load_error'));
    script.src=`${PUBLIC_API_URL}?callback=${encodeURIComponent(callbackName)}&_=${Date.now()}`;
    script.async=true;
    timer=setTimeout(()=>finish(reject,new Error('jsonp_timeout')),REQUEST_TIMEOUT_MS);
    document.head.appendChild(script);
  });
}

async function load(){
  if(refreshRunning) return;
  refreshRunning=true;
  let lastError=null;

  try{
    for(let attempt=1;attempt<=2;attempt++){
      try{
        const data=await requestJsonp();
        render(data);
        hasLiveData=true;
        consecutiveFailures=0;
        return;
      }catch(error){
        lastError=error;
        recordFailure(error,attempt);
        if(attempt===1) await delay(RETRY_DELAY_MS);
      }
    }

    consecutiveFailures+=1;
    showConnectionFailure(lastError);
  }finally{
    refreshRunning=false;
    setTimeout(load,REFRESH_MS);
  }
}

load();
