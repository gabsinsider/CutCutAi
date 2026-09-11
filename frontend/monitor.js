(()=>{
  const WORKER_URL='https://cutcutai-production.up.railway.app';
  const el=id=>document.getElementById(id);
  const monitor=el('live-monitor'),badge=el('monitor-badge');
  if(!monitor||!badge)return;
  let sessionStartedAt=null,lastRunning=false;

  function duration(seconds){
    seconds=Math.max(0,Math.floor(seconds||0));
    const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),s=seconds%60;
    return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(s).padStart(2,'0')}s`;
  }
  function sessionStart(data){
    const state=data?.state||{},session=state.session||{};
    return session.started_at||session.resumed_at||session.last_started_at||state.started_at||state.started||null;
  }
  function storageFrom(data){
    return data?.storage||data?.state?.storage||{};
  }
  function isRunning(data){
    return !!(data?.state?.running ?? data?.running);
  }
  function render(data){
    const running=isRunning(data);
    lastRunning=running;monitor.hidden=!running;
    if(!running){sessionStartedAt=null;return}
    sessionStartedAt=sessionStart(data)||sessionStartedAt;
    badge.textContent='● Processando';badge.dataset.state='running';
    const ready=Number(data?.capture?.ready_segments||0);
    const local=Number(data?.clips?.local||0),archived=Number(data?.clips?.archived||0);
    const storage=storageFrom(data);
    el('metric-buffer').textContent=ready?`${ready} bloco${ready===1?'':'s'}`:'Aguardando';
    el('metric-clips').textContent=String(local+archived);
    const free=Number(storage.free_mb),used=Number(storage.used_percent);
    el('metric-storage').textContent=Number.isFinite(free)?`${free>=1024?(free/1024).toFixed(1)+' GB':free.toFixed(0)+' MB'}`:'—';
    el('metric-storage-detail').textContent=Number.isFinite(used)?`${used.toFixed(1)}% do volume em uso`:'volume do worker';
    updateClock();
  }
  function updateClock(){
    if(!sessionStartedAt||monitor.hidden){el('metric-time').textContent='—';return}
    const start=Date.parse(sessionStartedAt);
    el('metric-time').textContent=Number.isFinite(start)?duration((Date.now()-start)/1000):'—';
  }
  async function json(path){
    const r=await fetch(`${WORKER_URL}${path}${path.includes('?')?'&':'?'}ts=${Date.now()}`,{cache:'no-store'});
    if(!r.ok)throw new Error(String(r.status));return r.json();
  }
  async function refresh(){
    try{
      const data=await json('/diagnostics');render(data);
    }catch{
      try{
        const status=await json('/status');
        if(!isRunning(status)){render(status);return}
        monitor.hidden=false;lastRunning=true;sessionStartedAt=sessionStart(status)||sessionStartedAt;
        badge.textContent='● Processando';badge.dataset.state='running';updateClock();
      }catch{
        if(lastRunning&&!monitor.hidden){badge.textContent='Reconectando…';badge.dataset.state='idle'}
      }
    }
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
  window.addEventListener('online',refresh);
  refresh();setInterval(refresh,10000);setInterval(updateClock,1000);
})();
