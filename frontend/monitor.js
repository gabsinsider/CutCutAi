(()=>{
  const WORKER_URL='https://cutcutai-production.up.railway.app';
  const el=id=>document.getElementById(id);
  const monitor=el('live-monitor'),badge=el('monitor-badge');
  let sessionStartedAt=null;

  function duration(seconds){
    seconds=Math.max(0,Math.floor(seconds||0));
    const h=Math.floor(seconds/3600),m=Math.floor((seconds%3600)/60),s=seconds%60;
    return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(s).padStart(2,'0')}s`;
  }
  function sessionStart(data){
    const s=data?.state?.session||{};
    return s.started_at||s.resumed_at||s.last_started_at||null;
  }
  function render(data){
    const running=!!data?.state?.running;
    monitor.hidden=!running;
    if(!running){sessionStartedAt=null;return}
    sessionStartedAt=sessionStart(data)||sessionStartedAt;
    badge.textContent='● Processando';badge.dataset.state='running';
    const ready=Number(data?.capture?.ready_segments||0);
    const local=Number(data?.clips?.local||0),archived=Number(data?.clips?.archived||0);
    const storage=data?.state?.storage||{};
    el('metric-buffer').textContent=ready?`${ready} bloco${ready===1?'':'s'}`:'Aguardando';
    el('metric-clips').textContent=String(local+archived);
    el('metric-storage').textContent=Number.isFinite(Number(storage.free_mb))?`${Number(storage.free_mb).toFixed(0)} MB`:'—';
    el('metric-storage-detail').textContent=Number.isFinite(Number(storage.used_percent))?`${Number(storage.used_percent).toFixed(1)}% do volume em uso`:'volume do worker';
    updateClock();
  }
  function updateClock(){
    if(!sessionStartedAt||monitor.hidden){el('metric-time').textContent='—';return}
    const start=Date.parse(sessionStartedAt);
    el('metric-time').textContent=Number.isFinite(start)?duration((Date.now()-start)/1000):'—';
  }
  async function refresh(){
    try{
      const r=await fetch(`${WORKER_URL}/diagnostics?ts=${Date.now()}`,{cache:'no-store'});
      if(!r.ok)throw new Error();render(await r.json());
    }catch{
      if(!monitor.hidden){badge.textContent='Reconectando…';badge.dataset.state='idle'}
    }
  }
  refresh();setInterval(refresh,10000);setInterval(updateClock,1000);
})();
