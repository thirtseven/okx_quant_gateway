let appState = {
  mode: "safe",
  safe_users: [],
  running_accounts: [],
  trading_pairs: ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","XRP-USDT-SWAP","DOGE-USDT-SWAP","ADA-USDT-SWAP","AVAX-USDT-SWAP","LINK-USDT-SWAP","POL-USDT-SWAP","DOT-USDT-SWAP","LTC-USDT-SWAP","UNI-USDT-SWAP","ATOM-USDT-SWAP","THETA-USDT-SWAP","EOS-USDT-SWAP","XLM-USDT-SWAP","TRX-USDT-SWAP","FIL-USDT-SWAP","NEAR-USDT-SWAP","APT-USDT-SWAP","ARB-USDT-SWAP","OP-USDT-SWAP","SAND-USDT-SWAP","MANA-USDT-SWAP","AAVE-USDT-SWAP","SHIB-USDT-SWAP","GRT-USDT-SWAP","RNDR-USDT-SWAP","MKR-USDT-SWAP","SUI-USDT-SWAP","STX-USDT-SWAP","INJ-USDT-SWAP","FET-USDT-SWAP"],
  strategy_active: false,
  strategy_log: [],
  price_cache: {},
  last_error: null,
  open_positions: []
};
let csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || '';
let allInstruments = [];
const POPULAR_PAIRS = ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","XRP-USDT-SWAP","DOGE-USDT-SWAP","ADA-USDT-SWAP","AVAX-USDT-SWAP","LINK-USDT-SWAP","POL-USDT-SWAP","DOT-USDT-SWAP","LTC-USDT-SWAP","UNI-USDT-SWAP","ATOM-USDT-SWAP","THETA-USDT-SWAP","EOS-USDT-SWAP","XLM-USDT-SWAP","TRX-USDT-SWAP","FIL-USDT-SWAP","NEAR-USDT-SWAP","APT-USDT-SWAP","ARB-USDT-SWAP","OP-USDT-SWAP","SAND-USDT-SWAP","MANA-USDT-SWAP","AAVE-USDT-SWAP","SHIB-USDT-SWAP","GRT-USDT-SWAP","RNDR-USDT-SWAP","MKR-USDT-SWAP","SUI-USDT-SWAP","STX-USDT-SWAP","INJ-USDT-SWAP","FET-USDT-SWAP","WOO-USDT-SWAP","PEPE-USDT-SWAP","WLD-USDT-SWAP","JTO-USDT-SWAP","BONK-USDT-SWAP","AI-USDT-SWAP","TAO-USDT-SWAP","RENDER-USDT-SWAP","FET-USDT-SWAP","NEAR-USDT-SWAP","APT-USDT-SWAP"];
let lastSignals = {};
let lastSignalsPrev = {};
function escapeHtml(s){if(typeof s!=='string')return s;return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function fetchWithCsrf(url,options={}){
  options.headers=options.headers||{};
  options.headers['X-CSRF-Token']=csrfToken;
  const sep = url.includes('?') ? '&' : '?';
  return fetch(url + sep + 'csrf_token=' + encodeURIComponent(csrfToken), options);
}

function loadState(){
  fetch('/api/state').then(r=>r.json()).then(d=>{
    appState=d;
    renderPairs();renderUsers();renderStrategyUI();renderQuickAdd();pollPositions();
    document.getElementById('stat-mode').textContent=appState.mode.toUpperCase();
    document.getElementById('mode-indicator').textContent=appState.mode==='safe'?'安全模式':'运行模式';
  });
  fetch('/api/accounts').then(r=>r.json()).then(d=>{
    appState.running_accounts=d.accounts;
    renderAccounts();
  });
}

function setupButtonHandlers(){
  document.addEventListener('click',function(e){
    const btn=e.target.closest('[data-pair]');
    if(!btn)return;
    const pair=decodeURIComponent(btn.dataset.pair);
    if(btn.classList.contains('btn-danger')){
      removePair(pair);
    }else if(btn.classList.contains('btn-outline')){
      addFromBrowser(pair);
    }
  });
}
setupButtonHandlers();

function updateClock(){
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('en-GB');
}
setInterval(updateClock,1000);updateClock();
let lastIndicatorUpdate = Date.now();
const INDICATOR_INTERVAL_MS = 900000;
function tickCountdown(){
  const elapsed = Date.now() - lastIndicatorUpdate;
  const remaining = Math.max(0, INDICATOR_INTERVAL_MS - (elapsed % INDICATOR_INTERVAL_MS));
  const secs = Math.floor(remaining / 1000);
  const el = document.getElementById('countdown-timer');
  if(el) el.textContent = '指标倒计时: ' + secs + 's';
}
setInterval(tickCountdown, 1000);tickCountdown();

function showPage(id){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  const targetPage=document.getElementById('page-'+id);
  if(targetPage)targetPage.classList.add('active');
  const targetNav=document.querySelector('.nav-item[data-page="'+id+'"]');
  if(targetNav)targetNav.classList.add('active');
}

function showAlert(msg,type='success'){
  const el=document.getElementById('global-alert');
  el.textContent=msg;el.className='alert alert-'+type+' show';
  setTimeout(()=>el.classList.remove('show'),3500);
}

function showError(msg){
  const el=document.getElementById('error-banner');
  el.textContent='ERROR: '+msg;el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'),8000);
}

function toggleStrategy(){
  fetch('/api/strategy/toggle',{method:'POST'}).then(r=>r.json()).then(d=>{
    appState.strategy_active=d.active;
    renderStrategyUI();
    showAlert(d.active?'策略已启动':'策略已暂停');
  });
}

function renderStrategyUI(){
  const t=document.getElementById('strategy-toggle');
  const lbl=document.getElementById('strategy-toggle-label');
  const txt=document.getElementById('strategy-status-text');
  const dot=document.getElementById('dot-live');
  if(appState.strategy_active){
    t.classList.add('on');lbl.textContent='运行中';
    txt.textContent='运行中 - 每15秒信号';
    dot.classList.add('live');
  }else{
    t.classList.remove('on');lbl.textContent='已暂停';
    txt.textContent='已暂停';
  }
}

function switchMode(mode){
  ['safe','running'].forEach(m=>{
    document.getElementById('mode-btn-'+m).classList.toggle('active',m===mode);
    document.getElementById('section-'+m).classList.toggle('active',m===mode);
  });
  fetch('/api/mode',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})})
    .then(r=>r.json()).then(d=>{
      appState.mode=d.mode;
      document.getElementById('mode-indicator').textContent=d.mode==='safe'?'安全模式':'实盘交易';
      document.getElementById('stat-mode').textContent=d.mode.toUpperCase();
      showAlert('模式: '+(d.mode==='safe'?'安全':'运行'));
    });
}

function addPair(){
  let v=document.getElementById('add-pair-input').value.trim().toUpperCase();
  if(!v)return showAlert('请输入币种名称','error');
  if(!v.includes('-'))v=v+'-USDT-SWAP';
  else if(!v.endsWith('-USDT-SWAP'))v=v.replace(/-USDT$/,'-USDT-SWAP');
  fetchWithCsrf('/api/pairs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pair:v})})
    .then(r=>r.json()).then(d=>{
      if(d.ok){appState.trading_pairs=d.pairs;renderPairs();renderQuickAdd();showAlert('已添加: '+v);document.getElementById('add-pair-input').value='';}
      else showAlert(d.msg,'error');
    });
}
function removePair(pair){
  fetchWithCsrf('/api/pairs/'+encodeURIComponent(pair),{method:'DELETE'})
    .then(r=>r.json()).then(d=>{appState.trading_pairs=d.pairs;renderPairs();renderQuickAdd();showAlert('已移除');});
}
function renderPairs(){
  const wrap=document.getElementById('active-pairs-wrap');
  if(!appState.trading_pairs.length){wrap.innerHTML='<div style="color:var(--muted);font-size:12px;padding:12px;">暂无交易对, 请使用浏览全部添加</div>';return;}
  const count=appState.trading_pairs.length;
  document.getElementById('pairs-count').textContent='('+count+' 个交易对)';
  document.getElementById('stat-pairs').textContent=count;
  wrap.innerHTML='<table><thead><tr><th>交易对</th><th></th></tr></thead><tbody>'+
    appState.trading_pairs.map(p=>'<tr><td style="font-family:var(--mono);color:var(--accent3);font-size:12px;">'+escapeHtml(p)+'</td><td style="text-align:right;"><button class="btn btn-danger btn-sm" data-pair="'+encodeURIComponent(p)+'">x</button></td></tr>').join('')+
    '</tbody></table>';
}
function renderQuickAdd(){
  const grid=document.getElementById('quick-add-grid');
  if(!grid)return;
  const active=new Set(appState.trading_pairs);
  const available=POPULAR_PAIRS.filter(p=>!active.has(p));
  grid.innerHTML=available.slice(0,24).map(p=>'<button class="btn btn-outline btn-sm" data-pair="'+encodeURIComponent(p)+'" style="font-size:10px;padding:4px 6px;">+'+p.replace('-USDT-SWAP','').replace('-USDT','')+'</button>').join('');
}
function quickAddPair(pair){
  fetchWithCsrf('/api/pairs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pair})})
    .then(r=>r.json()).then(d=>{if(d.ok){appState.trading_pairs=d.pairs;renderPairs();renderQuickAdd();showAlert('已添加: '+pair)}else showAlert(d.msg,'error');});
}

function addUser(){
  const email=document.getElementById('user-email').value.trim();
  if(!email){showAlert('请输入邮箱地址','error');return;}
  const body=JSON.stringify({email});
  fetchWithCsrf('/api/users',{method:'POST',headers:{'Content-Type':'application/json'},body})
    .then(r=>r.json()).then(d=>{
      if(d.ok){appState.safe_users=d.users;renderUsers();showAlert('邮箱已添加');document.getElementById('user-email').value='';}
      else showAlert(d.msg||'添加失败','error');
    }).catch(e=>showAlert('请求失败: '+e.message,'error'));
}

function renderUsers(){
  const el=document.getElementById('users-list');
  const btn=document.getElementById('btn-test-email');
  if(!appState.safe_users||!appState.safe_users.length){
    el.innerHTML='<div style="color:var(--muted);font-size:12px;padding:8px 0;">暂无通知邮箱</div>';
    if(btn)btn.style.display='none';
    return;
  }
  el.innerHTML=appState.safe_users.map((u,i)=>'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--bg3);border:1px solid #3a2a0a;border-radius:4px;margin-bottom:6px;"><div style="font-family:var(--mono);font-size:12px;">'+escapeHtml(u.email)+'</div><button class="btn btn-danger btn-sm" onclick="removeUser('+i+')">移除</button></div>').join('');
  if(btn)btn.style.display='';
}

function removeUser(idx){
  fetchWithCsrf('/api/users/'+idx,{method:'DELETE'}).then(r=>r.json()).then(d=>{appState.safe_users=d.users;renderUsers();});
}
function testEmail(){
  const btn=document.getElementById('btn-test-email');
  if(btn){btn.disabled=true;btn.textContent='发送中...';}
  fetchWithCsrf('/api/test-email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})})
    .then(r=>r.json()).then(d=>{
      if(btn){btn.disabled=false;btn.textContent='发送测试邮件';}
      if(d.ok){showAlert('测试邮件已发送: '+d.sent+' 封');}
      else{showAlert(d.msg||'发送失败','error');}
    }).catch(e=>{if(btn){btn.disabled=false;btn.textContent='发送测试邮件';}showAlert('发送失败','error');});
}
function addAccount(){
  const label=document.getElementById('acc-label').value.trim();
  const uid=document.getElementById('acc-uid').value.trim();
  const api_key=document.getElementById('acc-key').value.trim();
  const api_secret=document.getElementById('acc-secret').value.trim();
  const passphrase=document.getElementById('acc-pass').value.trim();
  if(!uid||!api_key||!api_secret||!passphrase)return showAlert('请填写所有API字段','error');
  fetchWithCsrf('/api/accounts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,uid,api_key,api_secret,passphrase})})
    .then(r=>r.json()).then(d=>{
      if(d.ok){appState.running_accounts=d.accounts;renderAccounts();showAlert('账户已添加');['acc-label','acc-uid','acc-key','acc-secret','acc-pass'].forEach(id=>document.getElementById(id).value='');}
      else showAlert(d.msg,'error');
    });
}
function removeAccount(idx){
  fetchWithCsrf('/api/accounts/'+idx,{method:'DELETE'}).then(r=>r.json()).then(d=>{appState.running_accounts=d.accounts;renderAccounts();});
}
function renderAccounts(){
  const el=document.getElementById('accounts-list');
  if(!appState.running_accounts.length){el.innerHTML='<div style="color:var(--muted);font-size:12px;">暂无账户</div>';return;}
  el.innerHTML=appState.running_accounts.map((a,i)=>'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 12px;background:var(--bg3);border:1px solid #3a2a0a;border-radius:4px;margin-bottom:6px;"><div><div style="font-family:var(--mono);font-size:12px;">'+escapeHtml(a.label||'账户 '+(i+1))+' <span style="color:var(--muted)">UID: '+escapeHtml(a.uid)+'</span></div><div style="font-size:10px;color:var(--muted);">API: '+escapeHtml(a.api_key.substring(0,8))+'...</div></div><button class="btn btn-danger btn-sm" onclick="removeAccount('+i+')">移除</button></div>').join('');
}

function renderOverviewPrices(data){
  const tbody=document.getElementById('overview-tbody');
  const err=appState.last_error;
  if(err){tbody.innerHTML='<tr><td colspan="5" style="color:var(--red);text-align:center;padding:16px;">API错误: '+escapeHtml(err.substring(0,60))+'</td></tr>';return;}
  const keys=Object.keys(data);
  if(!keys.length){tbody.innerHTML='<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:16px;">无价格数据 - 请检查网络</td></tr>';return;}
  tbody.innerHTML=keys.map(k=>{const t=data[k];const ts=t.ts?new Date(parseInt(t.ts)).toLocaleTimeString('en-GB'):'--';const last=parseFloat(t.last)||0;const open=parseFloat(t.open24h)||0;const vol=parseFloat(t.vol24h)||0;return'<tr><td style="color:var(--accent3);font-weight:500">'+escapeHtml(t.inst_id)+'</td><td class="price-val">'+(last?last.toLocaleString():'--')+'</td><td>'+(open?open.toLocaleString():'--')+'</td><td>'+(vol?vol.toLocaleString(undefined,{maximumFractionDigits:0}):'--')+'</td><td style="color:var(--muted)">'+ts+'</td></tr>'}).join('');
}

function renderPricesPage(data){
  const tbody=document.getElementById('prices-tbody');
  const keys=Object.keys(data);
  if(!keys.length){tbody.innerHTML='<tr><td colspan="6" style="color:var(--muted);text-align:center;padding:16px;">暂无数据</td></tr>';return;}
  tbody.innerHTML=keys.map(k=>{const t=data[k];const ts=t.ts?new Date(parseInt(t.ts)).toLocaleTimeString():'--';const last=parseFloat(t.last)||0;const open=parseFloat(t.open24h)||0;const vol=parseFloat(t.vol24h)||0;return'<tr><td style="color:var(--accent3);font-weight:500">'+escapeHtml(t.inst_id)+'</td><td class="price-val">'+(last?last.toLocaleString():'--')+'</td><td>'+(open?open.toLocaleString():'--')+'</td><td>'+(vol?vol.toLocaleString():'--')+'</td><td style="color:var(--muted)">'+ts+'</td><td><button class="btn btn-danger btn-sm" data-pair="'+encodeURIComponent(t.inst_id)+'">移除</button></td></tr>'}).join('');
}

function pollPrices(){
  fetch('/api/prices').then(r=>r.json()).then(d=>{
    appState.price_cache=d.prices||{};
    appState.last_error=d.error||null;
    renderOverviewPrices(appState.price_cache);
    renderPricesPage(appState.price_cache);
    const prices=d.prices||{};
    (appState.open_positions||[]).forEach(p=>{
      const el=document.getElementById('price-'+p.pair);
      if(el){
const ticker=prices[p.pair];
el.textContent=ticker?(parseFloat(ticker.last)||'-'):'-';
      }
    });
  }).catch(e=>{
    appState.last_error='价格获取失败';
  });
}
setInterval(pollPrices,30000);pollPrices();

function pollPositions(){
  fetch('/api/positions').then(r=>r.json()).then(d=>{
    const open=d.open_positions||[];
    appState.open_positions=open;
    const listEl=document.getElementById('pos-open-list');
    const histEl=document.getElementById('pos-history');
    document.getElementById('pos-open').textContent=d.open_count;
    document.getElementById('pos-max').textContent='/ '+d.max_positions+' 最大';
    document.getElementById('pos-winrate').textContent=d.win_rate+'%';
    document.getElementById('pos-record').textContent=d.wins+'胜 / '+d.losses+'负';
    document.getElementById('pos-losses').textContent=d.consecutive_losses;
    const circuit=d.circuit_broken;
    document.getElementById('pos-circuit').textContent=circuit?'熔断: 开启':'熔断: 关闭';
    document.getElementById('pos-circuit').style.color=circuit?'var(--red)':'var(--muted)';
    document.getElementById('pos-circuit-card').style.display=circuit?'block':'none';
    const totalPnl=d.total_pnl||0;
    document.getElementById('pos-summary').textContent='持仓:'+d.open_count+'/5 | 胜率:'+d.win_rate+'% | 连亏:'+d.consecutive_losses+'/5 | 总收益:'+(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'%'+(circuit?' | 熔断器':'');
    document.getElementById('pos-summary').style.color=circuit?'var(--red)':'var(--muted)';
    // Display strategy params
    const stEl=document.getElementById('strategy-status-text');
    if(stEl && d.strategy){
      const sm=d.strategy.score_min||d.strategy.score_threshold||'?';
      const rm=d.strategy.regime_mode||'auto';
      fetch('/api/strategy/model').then(r=>r.json()).then(m=>{
        const version=m.loaded?m.version:'未加载';
        stEl.textContent=version+' | '+rm+' | Score≥'+sm;
      }).catch(()=>{stEl.textContent=rm+' | Score≥'+sm;});
    }
    if(!open.length){
      listEl.innerHTML='<div style="color:var(--muted);font-size:12px;padding:12px;">暂无持仓</div>';
    }else{
      listEl.innerHTML='<table><thead><tr><th>交易对</th><th>方向</th><th>入场价</th><th>当前价</th><th>止损</th><th>止盈</th><th>状态</th></tr></thead><tbody>'+
open.map(p=>'<tr><td style="font-family:var(--mono);color:var(--accent3)">'+escapeHtml(p.pair)+'</td><td style="color:'+(p.direction==="LONG"?"var(--accent)":"var(--red)")+';font-weight:600">'+escapeHtml(p.direction)+'</td><td>'+p.entry.toFixed(4)+'</td><td id="price-'+escapeHtml(p.pair)+'" style="color:var(--accent);font-weight:500">-</td><td>'+p.sl.toFixed(4)+'</td><td>'+p.tp.toFixed(4)+'</td><td><span style="color:var(--yellow);font-size:10px">持仓中</span></td></tr>').join('')+
'</tbody></table>';
    }
    const hist=d.history||[];
    if(!hist.length){
      histEl.innerHTML='<div style="color:var(--muted);font-size:12px;padding:12px;">暂无平仓记录</div>';
    }else{
      histEl.innerHTML='<table><thead><tr><th>时间</th><th>交易对</th><th>方向</th><th>入场价</th><th>出场价</th><th>盈亏%</th></tr></thead><tbody>'+
hist.slice().reverse().map(h=>'<tr><td style="color:var(--muted);font-size:11px">'+escapeHtml(h.closed_at)+'</td><td style="font-family:var(--mono);color:var(--accent3)">'+escapeHtml(h.pair)+'</td><td style="color:'+(h.direction==="LONG"?"var(--accent)":"var(--red)")+'">'+escapeHtml(h.direction)+'</td><td>'+h.entry.toFixed(4)+'</td><td>'+h.exit.toFixed(4)+'</td><td style="color:'+(h.win?"var(--accent)":"var(--red)")+';font-weight:600">'+(h.pnl>=0?'+':'')+h.pnl.toFixed(2)+'%</td></tr>').join('')+
'</tbody></table>';
    }
    const posSummary=document.getElementById('pos-summary');
  if(posSummary){
    const cb=d.circuit_broken;
    posSummary.textContent='持仓:'+d.open_count+'/5 | 胜率:'+d.win_rate+'% | 连亏:'+d.consecutive_losses+'/5'+(cb?' | 熔断器':'');
    posSummary.style.color=cb?'var(--red)':'var(--muted)';
  }
  }).catch(()=>{});
}

setInterval(pollPositions,60000);

function resetPositions(){
  if(!confirm('确认重置所有持仓和历史记录?'))return;
  document.getElementById('pos-open-list').innerHTML='<div style="color:var(--muted);font-size:12px;padding:12px;">已重置</div>';
  document.getElementById('pos-history').innerHTML='<div style="color:var(--muted);font-size:12px;padding:12px;">已重置</div>';
  fetchWithCsrf('/api/positions/reset',{method:'POST'}).then(r=>r.json()).then(d=>{pollPositions();showAlert('已重置持仓');}).catch(e=>showAlert('重置失败: '+e.message,'error'));
}

function closeAllPositions(){
  if(!confirm('确认平掉所有仓位?（将以当前市场价格市价全平）'))return;
  fetchWithCsrf('/api/positions/close-all',{method:'POST'})
    .then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
    .then(d=>{if(d.ok){pollPositions();showAlert('已全平仓位');}else{showAlert(d.msg||'失败','error');}})
    .catch(e=>{showAlert('平仓失败: '+e.message,'error');pollPositions();});
}

function pollLog(){
  fetch('/api/log').then(r=>r.json()).then(d=>{
    const box=document.getElementById('log-box');
    if(!d.log.length){box.innerHTML='<div style="color:var(--muted)">暂无信号</div>';return;}
    box.innerHTML=d.log.flatMap(e=>e.signals.map(s=>'<div class="log-line"><span class="log-time">['+escapeHtml(e.time)+']</span><span class="log-signal">'+escapeHtml(s.replace(/\\[.*?\\]/,''))+'</span></div>')).join('');
    document.getElementById('stat-signals').textContent=d.log.reduce((a,e)=>a+e.signals.length,0);
  });
}
setInterval(pollLog,6000);pollLog();
function clearLog(){fetchWithCsrf('/api/log/clear',{method:'POST'}).then(()=>pollLog());}

function loadInstruments(){
  fetch('/api/instruments').then(r=>r.json()).then(d=>{
    allInstruments=d.instruments;
    filterInstruments();
    const btn=document.getElementById('btn-add-all');
    if(btn&&allInstruments.length)btn.style.display='';
  });
}
function filterInstruments(){
  const q=document.getElementById('inst-search').value.toLowerCase();
  const filtered=allInstruments.filter(i=>i.toLowerCase().includes(q));
  const tbody=document.getElementById('inst-tbody');
  if(!filtered.length){tbody.innerHTML='<tr><td colspan="3" style="color:var(--muted);text-align:center;padding:16px;">无结果</td></tr>';return;}
  tbody.innerHTML=filtered.slice(0,80).map(i=>{
    const active=appState.trading_pairs.includes(i);
    return'<tr><td style="font-family:var(--mono);color:var(--accent3)">'+escapeHtml(i)+'</td><td>'+(active?'<span style="color:var(--accent2);font-size:10px">已添加</span>':'<span style="color:var(--muted);font-size:10px">未添加</span>')+'</td><td>'+(active?'<button class="btn btn-danger btn-sm" data-pair="'+encodeURIComponent(i)+'">移除</button>':'<button class="btn btn-outline btn-sm" data-pair="'+encodeURIComponent(i)+'">+ 添加</button>')+'</td></tr>';
  }).join('');
}
function addFromBrowser(pair){
  fetch('/api/pairs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pair})})
    .then(r=>r.json()).then(d=>{if(d.ok){appState.trading_pairs=d.pairs;renderPairs();renderQuickAdd();filterInstruments();showAlert('已添加: '+pair)}else showAlert(d.msg,'error');});
}
function addAllInstruments(){
  const btn=document.getElementById('btn-add-all');
  if(!btn)return;
  btn.disabled=true;btn.textContent='添加中...';
  fetchWithCsrf('/api/pairs/add-all',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})})
    .then(r=>r.json()).then(d=>{
      btn.disabled=false;btn.textContent='一键添加全部';
      if(d.ok){
appState.trading_pairs=d.list.concat(appState.trading_pairs.filter(p=>d.list.includes(p)));
renderPairs();renderQuickAdd();filterInstruments();
showAlert('已添加: '+d.added+' | 跳过(价格0): '+d.skipped+' | 失败: '+d.failed);
      } else {
showAlert(d.msg||'添加失败','error');
      }
    }).catch(e=>{btn.disabled=false;btn.textContent='一键添加全部';showAlert('添加失败','error');});
}
function loadIndicatorTiming(){
  fetch('/api/indicators').then(r=>r.json()).then(d=>{
    if(d.refreshing){return;}
    if(d.last_update && d.last_update !== 'never'){
      const parts = d.last_update.split(':');
      if(parts.length === 3){
const serverTime = new Date();
serverTime.setHours(parseInt(parts[0]), parseInt(parts[1]), parseInt(parts[2]), 0);
const now = new Date();
const serverMs = serverTime.getTime() - (serverTime.getTimezoneOffset() - now.getTimezoneOffset()) * 60000;
lastIndicatorUpdate = serverMs;
      } else {
lastIndicatorUpdate = Date.now();
      }
    } else {
      lastIndicatorUpdate = Date.now();
    }
    const t=d.timing;
    const el=document.getElementById('data-timing');
    if(el){
      el.textContent='Data: '+d.last_update+' | Fetch:'+t.fetch_ms+'ms Calc:'+t.calc_ms+'ms Total:'+t.total_ms+'ms | Pairs:'+t.pairs;
    }
  }).catch(()=>{});
}
function triggerSignalsRefresh(){
  if(isCalculating)return;
  pollSignals();
}

function applyThresholds(){
  const bull=parseFloat(document.getElementById('th-bull').value);
  const bear=parseFloat(document.getElementById('th-bear').value);
  const range=parseFloat(document.getElementById('th-range').value);
  const vals=[bull,bear,range];
  if(vals.some(v=>isNaN(v)||v<0.1||v>3.0)){showAlert('阈值范围: 0.1~3.0','error');return;}
  const regimes=['bull','bear','range'];
  let done=0;
  Promise.all(regimes.map((r,i)=>fetch('/api/strategy/params/regimes.'+r+'.score_min',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:vals[i]})}).then(r=>r.json())))
    .then(results=>{
      const ok=results.filter(r=>r.ok).length;
      if(ok===3){showAlert('阈值已更新: Bull='+bull+' Bear='+bear+' Range='+range);pollSignals();}
      else showAlert('部分更新失败','error');
    }).catch(()=>showAlert('更新失败','error'));
}

function applyFlatThreshold(){
  const val=parseFloat(document.getElementById('th-flat').value);
  if(isNaN(val)||val<0.1||val>3.0){showAlert('阈值范围: 0.1~3.0','error');return;}
  fetch('/api/strategy/params/score_min',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:val})})
    .then(r=>r.json()).then(d=>{
      if(d.ok){showAlert('阈值已更新: '+val);pollSignals();}
      else showAlert(d.msg||'更新失败','error');
    }).catch(()=>showAlert('更新失败','error'));
}

function renderSignalList(d){
  const listEl=document.getElementById('signal-list');
  if(!listEl)return;
  const pairs=Object.entries(d.signals||{});
  if(!pairs.length){listEl.innerHTML='<div style="color:var(--muted);padding:12px;">无信号数据</div>';return;}

  const sorted = pairs.sort((a,b)=>b[1].norm_long - a[1].norm_long);

  listEl.innerHTML='<table style="width:100%;border-collapse:collapse;"><thead><tr style="border-bottom:1px solid #3a2a0a;"><th style="text-align:left;padding:5px 3px;color:var(--muted);font-size:9px;">交易对</th><th style="text-align:center;padding:5px 3px;color:var(--muted);font-size:9px;">信号</th><th style="text-align:right;padding:5px 3px;color:var(--muted);font-size:9px;">模型得分</th><th style="text-align:right;padding:5px 3px;color:var(--muted);font-size:9px;">RSI14</th><th style="text-align:right;padding:5px 3px;color:var(--muted);font-size:9px;">收盘</th></tr></thead><tbody>'+
    sorted.map(([pair,s])=>{
      const score=s.norm_long>0?s.norm_long:-s.norm_short;
      const scoreStr=score.toFixed(1);
      const rsi=s.rsi_14!=null?s.rsi_14.toFixed(1):'--';
      const close=s.close!=null?s.close.toFixed(2):'--';
      const color = s.signal == "LONG" ? "var(--accent)" : s.signal == "SHORT" ? "var(--red)" : "var(--muted)";
      return '<tr style="border-bottom:1px solid #1a1208;"><td style="font-family:var(--mono);color:var(--accent3);padding:4px 3px;font-size:11px;">'+escapeHtml(pair)+'</td><td style="text-align:center;color:'+color+';font-weight:700;font-size:11px;">'+escapeHtml(s.signal)+'</td><td style="text-align:right;color:var(--accent);font-size:11px;">'+scoreStr+'</td><td style="text-align:right;color:var(--muted);font-size:10px;">'+rsi+'</td><td style="text-align:right;color:var(--accent3);font-size:10px;">'+close+'</td></tr>';
    }).join('')+
    '</tbody></table>';
  const sigCount=document.getElementById('signal-count');
  if(sigCount){
    const long=pairs.filter(p=>p[1].signal=="LONG").length;
    const short=pairs.filter(p=>p[1].signal=="SHORT").length;
    sigCount.innerHTML='('+long+' 多头 / '+short+' 空头 / '+pairs.length+' 总计)';
  }
}

let isCalculating = false;

function pollIndicators(){
  fetch('/api/indicators').then(r=>r.json()).then(d=>{
    document.getElementById('data-timing').textContent =
      'fetch:'+d.timing.fetch_ms+'ms calc:'+d.timing.calc_ms+'ms';
  }).catch(()=>{});
}

function pollSignals(){
  const calcStart = Date.now();
  isCalculating = true;
  // Show computing state immediately, clear stale thresholds
  const listEl=document.getElementById('signal-list');
  const sigCount=document.getElementById('signal-count');
  const thEl=document.getElementById('signal-thresholds');
  if(listEl){listEl.innerHTML='<div style="color:var(--accent);padding:16px;text-align:center;font-size:13px;">正在计算中，请稍候...</div>';}
  if(sigCount){sigCount.textContent='(计算中...)';}
  // Clear thresholds to avoid showing stale values
  if(thEl){thEl.innerHTML='<div style="color:var(--muted);padding:8px 0;font-size:12px;text-align:center;">计算中...</div>';}
  // Disable manual refresh button
  const refreshBtn=document.getElementById('refresh-signals-btn');
  if(refreshBtn){refreshBtn.disabled=true;refreshBtn.style.opacity='0.5';}
  fetch('/api/signals').then(r=>r.json()).then(d=>{
    isCalculating = false;
    // Re-enable manual refresh
    const refreshBtn=document.getElementById('refresh-signals-btn');
    if(refreshBtn){refreshBtn.disabled=false;refreshBtn.style.opacity='1';}
    if(d.error){
      if(listEl){listEl.innerHTML='<div style="color:var(--red);padding:12px;">错误: '+escapeHtml(d.error)+'</div>';}
      if(sigCount){sigCount.textContent='(错误)';}
      return;
    }
    lastSignalsPrev = lastSignals.signals || {};
    lastSignals=d;
    const th=d.thresholds;
    if(thEl){
      fetch('/api/strategy/model').then(r=>r.json()).then(m=>{
        let html='<div style="font-size:11px;color:var(--text2);">';
        if(m.loaded){
          html+='LightGBM '+m.version+' | '+m.champion+'<br>';
          html+='树深:'+m.model.max_depth+' 树数:'+m.model.n_estimators+' 叶子:'+m.model.num_leaves+'<br>';
          html+='特征:'+m.n_features+' | WR:'+(m.test_wr*100).toFixed(0)+'% PF:'+m.test_pf.toFixed(2)+'<br>';
          html+='持仓:'+m.trade.max_hold_bars+'bars | 仅做多';
        }else{
          html+='模型未加载';
        }
        html+='</div>';
        thEl.innerHTML=html;
      }).catch(()=>{thEl.innerHTML='<div style="color:var(--muted);">加载中...</div>';});
    }
    renderSignalList(d);
  }).catch(e=>{
    isCalculating = false;
    const refreshBtn=document.getElementById('refresh-signals-btn');
    if(refreshBtn){refreshBtn.disabled=false;refreshBtn.style.opacity='1';}
    if(listEl){listEl.innerHTML='<div style="color:var(--red);padding:12px;">请求失败: '+escapeHtml(String(e))+'</div>';}
  });
}
function triggerSignalsRefresh(){
  if(isCalculating)return;
  pollSignals();
}
setInterval(pollSignals, 20000); // 20s auto-refresh
pollSignals();

loadInstruments();
loadIndicatorTiming();
loadState();
