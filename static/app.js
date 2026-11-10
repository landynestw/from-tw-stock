const $ = s => document.querySelector(s);
let investor = 'foreign', direction = 'buy', descending = true, timer;
const fmt = n => Number(n || 0).toLocaleString('en-US');
const dateFmt = d => d ? new Date(`${d}T00:00:00`).toLocaleDateString('zh-TW',{year:'numeric',month:'2-digit',day:'2-digit'}) : '尚無資料';
const timeFmt = d => d ? new Date(d).toLocaleString('zh-TW',{hour12:false}) : '尚未更新';

async function getJSON(url, options) {
  const r = await fetch(url, options); const data = await r.json();
  if (!r.ok) throw new Error(data.error || `資料讀取失敗（HTTP ${r.status}）`);
  return data;
}
function showNotice(text, error=false) { const n=$('#notice'); n.textContent=text; n.className=`notice${error?' error':''}`; n.hidden=!text; }
async function loadStatus() {
  try {
    const s=await getJSON('/api/status'); $('#dataDate').textContent=dateFmt(s.data_date); $('#updatedAt').textContent=timeFmt(s.updated_at);
    if (s.update_status==='running') showNotice(s.update_message);
    else if (s.update_status==='error') showNotice(`上次更新失敗：${s.update_message}。目前保留並顯示最近一次成功取得的資料。`,true);
    else if (!s.data_date) showNotice('尚無本機資料。請按「更新資料」取得 TWSE 盤後資料。');
    else if (s.is_stale) showNotice(`目前顯示最近一個有資料的交易日：${dateFmt(s.data_date)}。最新交易日資料可能尚未公布，或尚未手動更新。`);
    return s;
  } catch(e) { showNotice(e.message,true); }
}
async function loadRankings() {
  const body=$('#rankingRows'); body.innerHTML='<tr><td colspan="6" class="empty">載入排行中…</td></tr>';
  try {
    const params=new URLSearchParams({investor,direction,q:$('#search').value});
    const d=await getJSON(`/api/rankings?${params}`);
    if (!d.rows.length) { body.innerHTML=`<tr><td colspan="6" class="empty">${d.data_date?'此條件沒有符合的股票。':'尚無資料，請先更新。'}</td></tr>`; return; }
    const rows=d.rows.slice().sort((a,b)=>descending?b.net_shares-a.net_shares:a.net_shares-b.net_shares);
    body.innerHTML=rows.map(r=>`<tr><td class="stock-name"><button data-code="${r.code}"><span class="stock-line"><span class="code">${r.code}</span><span class="name">${escapeHTML(r.name)}</span></span></button></td><td>${fmt(r.buy_shares)}</td><td>${fmt(r.sell_shares)}</td><td class="num ${r.net_shares>=0?'pos':'neg'}">${fmt(r.net_shares)}</td><td class="lot">${(r.net_shares/1000).toLocaleString('zh-TW',{maximumFractionDigits:2})}</td><td>${dateFmt(r.trade_date)}</td></tr>`).join('');
    body.querySelectorAll('[data-code]').forEach(b=>b.addEventListener('click',()=>loadStock(b.dataset.code)));
  } catch(e) { body.innerHTML=`<tr><td colspan="6" class="empty">${escapeHTML(e.message)}</td></tr>`; }
}
function escapeHTML(s) { return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function chart(days) {
  const w=900,h=250,left=47,right=10,top=16,bottom=32,plotW=w-left-right,plotH=h-top-bottom;
  const all=days.flatMap(d=>[d.foreign,d.trust,d.dealer,d.total]); const max=Math.max(1,...all.map(Math.abs)); const scale=max*1.12;
  const zero=top+plotH/2, colors=['#368d73','#d5a548','#6085bc','#263c34'], keys=['foreign','trust','dealer','total'];
  const step=plotW/days.length, barW=Math.max(2,Math.min(8,step*.14));
  let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="近二十個交易日法人每日買賣超股數圖表"><line class="chart-grid" x1="${left}" x2="${w-right}" y1="${zero}" y2="${zero}"/>`;
  for (let j=0;j<5;j++){const y=top+j*plotH/4;svg+=`<line class="chart-grid" x1="${left}" x2="${w-right}" y1="${y}" y2="${y}"/><text class="chart-label" x="${left-7}" y="${y+3}" text-anchor="end">${(scale*(1-j*0.5)/1000000).toFixed(1)}M</text>`;}
  days.forEach((d,i)=>{const groupX=left+i*step+step/2;keys.forEach((k,j)=>{const val=d[k], bh=Math.abs(val)/scale*plotH/2, x=groupX+(j-1.5)*barW*1.2-barW/2,y=val>=0?zero-bh:zero;svg+=`<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(1,bh)}" rx="1" fill="${colors[j]}" opacity=".88"><title>${d.date} ${['外資','投信','自營商','合計'][j]}：${fmt(val)} 股</title></rect>`;});if(i===0||i===days.length-1||i%4===0){const label=d.date.slice(5);svg+=`<text class="chart-label" x="${groupX}" y="${h-7}" text-anchor="middle">${label}</text>`;}});
  return svg+'</svg>';
}
function cumulativeChart(days) {
  const w=900,h=210,left=47,right=10,top=15,bottom=28,plotW=w-left-right,plotH=h-top-bottom;
  const keys=['foreign','trust','dealer','total'], colors=['#368d73','#d5a548','#6085bc','#263c34'];
  const values=keys.map(k=>{let sum=0;return days.map(d=>(sum+=d[k]));});
  const max=Math.max(1,...values.flat().map(Math.abs)), step=days.length>1?plotW/(days.length-1):0;
  const y=v=>top+(max-v)/(2*max)*plotH;
  let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="近二十個交易日法人區間累計買賣超趨勢"><line class="chart-grid" x1="${left}" x2="${w-right}" y1="${y(0)}" y2="${y(0)}"/>`;
  for(let j=0;j<5;j++){const v=max-j*max/2,yy=y(v);svg+=`<line class="chart-grid" x1="${left}" x2="${w-right}" y1="${yy}" y2="${yy}"/><text class="chart-label" x="${left-7}" y="${yy+3}" text-anchor="end">${(v/1000000).toFixed(1)}M</text>`;}
  values.forEach((series,j)=>{const pts=series.map((v,i)=>`${left+(days.length>1?i*step:plotW/2)},${y(v)}`).join(' ');svg+=`<polyline fill="none" stroke="${colors[j]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" points="${pts}"/>`;series.forEach((v,i)=>{const x=left+(days.length>1?i*step:plotW/2);svg+=`<circle cx="${x}" cy="${y(v)}" r="2.2" fill="${colors[j]}"><title>${days[i].date} 累計 ${fmt(v)} 股</title></circle>`;});});
  days.forEach((d,i)=>{if(i===0||i===days.length-1||i%4===0){const x=left+(days.length>1?i*step:plotW/2);svg+=`<text class="chart-label" x="${x}" y="${h-6}" text-anchor="middle">${d.date.slice(5)}</text>`;}});
  return svg+'</svg>';
}
async function loadStock(code) {
  $('#stockCode').value=code; const box=$('#stockContent');box.className='empty stock-empty';box.textContent='讀取個股資料中…';
  try {
    const d=await getJSON(`/api/stock/${encodeURIComponent(code)}`); const latest=d.days[d.days.length-1];
    box.className=''; box.innerHTML=`<div class="stock-meta"><strong>${escapeHTML(latest.code)}　${escapeHTML(latest.name)}</strong><span>近 ${d.days.length} 個交易日・股數</span></div><div class="legend"><span><i style="background:#368d73"></i>外資</span><span><i style="background:#d5a548"></i>投信</span><span><i style="background:#6085bc"></i>自營商</span><span><i style="background:#263c34"></i>三大法人合計</span></div><p class="table-note">每日買賣超（股）</p><div class="chart-wrap">${chart(d.days)}</div><p class="table-note">區間累計趨勢（股）</p><div class="chart-wrap cumulative-chart">${cumulativeChart(d.days)}</div><div class="cumulative">${[['外資','foreign'],['投信','trust'],['自營商','dealer'],['三大法人合計','total']].map(([name,k])=>`<div class="cum-card"><span>區間累計・${name}（股）</span><strong class="num ${d.cumulative[k]>=0?'pos':'neg'}">${fmt(d.cumulative[k])}</strong></div>`).join('')}</div><div class="table-wrap"><table><thead><tr><th>交易日期</th><th>外資（股）</th><th>投信（股）</th><th>自營商（股）</th><th>三大法人合計（股）</th></tr></thead><tbody>${d.days.slice().reverse().map(x=>`<tr><td>${dateFmt(x.date)}</td>${['foreign','trust','dealer','total'].map(k=>`<td class="num ${x[k]>=0?'pos':'neg'}">${fmt(x[k])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
    $('#stockPanel').scrollIntoView({behavior:'smooth',block:'start'});
  } catch(e) { box.className='empty stock-empty';box.textContent=e.message; }
}
$('#investorTabs').addEventListener('click',e=>{const b=e.target.closest('[data-investor]');if(!b)return;investor=b.dataset.investor;document.querySelectorAll('[data-investor]').forEach(x=>x.classList.toggle('active',x===b));loadRankings();});
document.querySelectorAll('[data-direction]').forEach(b=>b.addEventListener('click',()=>{direction=b.dataset.direction;descending=direction==='buy';document.querySelectorAll('[data-direction]').forEach(x=>x.classList.toggle('active',x===b));loadRankings();}));
$('#search').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(loadRankings,180);});
$('#sort').addEventListener('click',()=>{descending=!descending;loadRankings();});
$('#stockForm').addEventListener('submit',e=>{e.preventDefault();const code=$('#stockCode').value.trim();if(code)loadStock(code);});
$('#update').addEventListener('click',async()=>{const b=$('#update');b.disabled=true;b.textContent='更新中…';showNotice('正在逐日查詢 TWSE T86 並更新近 40 個日曆日，請稍候。');try{await getJSON('/api/update',{method:'POST'});showNotice('更新完成。');await loadStatus();await loadRankings();}catch(e){showNotice(`更新失敗：${e.message}。原有資料仍保留。`,true);await loadStatus();}finally{b.disabled=false;b.textContent='↻　更新資料';}});
loadStatus().then(loadRankings);
