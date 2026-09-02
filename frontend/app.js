const REPO = 'gabsinsider/CutCutAi';
let clips = [];

const $ = (selector) => document.querySelector(selector);
const toast = (message) => { const el=$('#toast'); el.textContent=message; el.classList.add('show'); setTimeout(()=>el.classList.remove('show'),2600); };

document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.nav-item,.view').forEach(el => el.classList.remove('active'));
  button.classList.add('active'); $(`#${button.dataset.view}`).classList.add('active');
}));

function issueUrl(title, body, labels='live-request') {
  const base=`https://github.com/${REPO}/issues/new`;
  return `${base}?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}&labels=${encodeURIComponent(labels)}`;
}

$('#live-form').addEventListener('submit', event => {
  event.preventDefault(); const url=$('#live-url').value.trim();
  const body=`## Link da live\n${url}\n\n## Configuração\n- Captura inicial: 180 segundos\n- Modelo: tiny\n`;
  window.open(issueUrl('[LIVE] Processar transmissão', body), '_blank', 'noopener');
  toast('Finalize o envio da Issue no GitHub.');
});

$('#live-url').addEventListener('input', event => {
  const value=event.target.value.toLowerCase(); let name='Plataforma compatível com yt-dlp';
  if(value.includes('youtu')) name='YouTube detectado'; else if(value.includes('tiktok')) name='TikTok detectado'; else if(value.includes('facebook')||value.includes('fb.watch')) name='Facebook detectado'; else if(value.includes('twitch')) name='Twitch detectado';
  $('#platform-hint').textContent=name;
});

function clipCard(clip, index) {
  const asset=clip.asset_url || '#'; const date=new Date(clip.created_at).toLocaleDateString('pt-BR');
  return `<article class="clip-card"><div class="thumb">${clip.thumbnail_url?`<img src="${clip.thumbnail_url}" alt="">`:''}<span class="score">#${index+1} · ${Math.round(clip.score)}</span></div><div class="clip-body"><h3>${escapeHtml(clip.title||'Corte sem título')}</h3><div class="meta"><span>${Math.round(clip.duration||60)}s</span><span>${date}</span></div><p class="tags">${(clip.hashtags||[]).join(' ')}</p><div class="card-actions"><a href="${asset}" ${asset==='#'?'aria-disabled="true"':'target="_blank"'}>Assistir</a><button data-edit="${clip.id}">Editar</button></div></div></article>`;
}

function escapeHtml(text) { const p=document.createElement('p'); p.textContent=text; return p.innerHTML; }

async function loadRanking() {
  try {
    const response=await fetch('data/ranking.json?ts='+Date.now()); if(!response.ok) throw new Error();
    clips=(await response.json()).clips||[]; $('#ranking-grid').innerHTML=clips.map(clipCard).join(''); $('#empty-state').hidden=clips.length>0;
    $('#clip-select').innerHTML='<option value="">Selecione…</option>'+clips.map(c=>`<option value="${c.id}">${escapeHtml(c.title)}</option>`).join('');
    document.querySelectorAll('[data-edit]').forEach(b=>b.addEventListener('click',()=>openEditor(b.dataset.edit)));
  } catch { $('#empty-state').hidden=false; toast('Não foi possível atualizar o ranking.'); }
}

function openEditor(id) { document.querySelector('[data-view="editor"]').click(); $('#clip-select').value=id; updatePreview(); }
function updatePreview() { const clip=clips.find(c=>c.id===$('#clip-select').value); const video=$('#preview'); if(clip?.asset_url){video.src=clip.asset_url;video.hidden=false;$('#video-placeholder').hidden=true}else{video.hidden=true;$('#video-placeholder').hidden=false} }
$('#clip-select').addEventListener('change',updatePreview); $('#refresh').addEventListener('click',loadRanking);
$('#resolution').addEventListener('change',event=>$('#quality-warning').hidden=event.target.value!=='2160');
$('#edit-form').addEventListener('submit', event => {
  event.preventDefault(); const id=$('#clip-select').value; if(!id) return;
  const body=`## Corte\n${id}\n\n## Edição\n- Legenda: ${$('#caption-style').value}\n- Filtro: ${$('#filter').value}\n- Resolução: ${$('#resolution').value}\n\n## Narração\n${$('#narration').value||'(sem narração)'}`;
  window.open(issueUrl(`[EDIT] Exportar ${id}`,body,'edit-request'),'_blank','noopener'); toast('Finalize o pedido de edição no GitHub.');
});
loadRanking();
