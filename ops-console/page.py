PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全模态网关 · 统一运维台</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:24px}
.wrap{max-width:900px;margin:0 auto}
h2{font-weight:600;margin:0 0 4px}
h3{font-size:15px;margin:20px 0 10px}
.sub{color:#888;font-size:13px;margin-bottom:20px}
.login{background:#171a21;border:1px solid #262b36;border-radius:10px;padding:16px;margin-bottom:16px}
input,textarea,button{font:inherit;border-radius:8px;border:1px solid #2c323f;background:#10131a;color:#e6e6e6;padding:9px 11px}
input{width:220px}
textarea{width:100%;box-sizing:border-box;min-height:64px;font-family:ui-monospace,monospace;font-size:12px}
button{cursor:pointer;background:#2563eb;border-color:#2563eb;color:#fff}
button.ghost{background:transparent;color:#9aa4b2;border-color:#2c323f}
button.mini{padding:2px 8px;font-size:11px}
button:disabled{opacity:.5;cursor:default}
.card{background:#171a21;border:1px solid #262b36;border-radius:12px;padding:15px 16px;margin-bottom:12px;overflow-x:auto}
.row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.ok{background:#22c55e}.bad{background:#ef4444}.warn{background:#eab308}.quota{background:#e5e7eb}.down{background:#6b7280}.err{background:#f97316}
.name{font-weight:600;width:150px}
.meta{color:#888;font-size:12px}
.grow{flex:1}
.hidden{display:none}
.msg{font-size:13px;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#7dd3fc;text-align:left;padding:4px 6px;font-weight:600}
td{padding:5px 6px;border-top:1px solid #262b36;vertical-align:middle}
details{background:#171a21;border:1px solid #262b36;border-radius:12px;padding:12px 16px;margin-top:12px}
summary{cursor:pointer;color:#9aa4b2;font-size:14px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:50}
.modal .box{background:#141720;border:1px solid #2c323f;border-radius:12px;padding:16px;width:min(860px,92vw);max-height:82vh;display:flex;flex-direction:column}
pre{background:#0b0d12;border:1px solid #262b36;border-radius:8px;padding:10px;overflow:auto;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-all;flex:1}
.hint{color:#666;font-size:11px;margin-top:4px;font-family:ui-monospace,monospace;word-break:break-all}
.chk{color:#22c55e}.nox{color:#ef4444}
a{color:#7dd3fc}
</style></head><body><div class="wrap">
<h2>全模态网关 · 统一运维台</h2>
<div class="sub">一个入口：连接信息 / 适配器 / 基础组件 / 渠道调权 / 用量 / 备份 / 体检。全部本机操作，不打模型接口。</div>

<div id="login" class="login">
  <div class="row">
    <input id="pw" type="password" placeholder="运维台密码" onkeydown="if(event.key==='Enter')login()">
    <button onclick="login()">进入</button>
  </div>
</div>

<div id="panel" class="hidden">
  <div class="row" style="margin-bottom:6px">
    <button onclick="doHealth(this)">一键体检</button>
    <button class="ghost" onclick="load()">刷新全部</button>
    <label class="meta"><input type="checkbox" id="auto" checked style="width:auto;vertical-align:-2px"> 每30秒自动刷新</label>
    <div class="grow"></div>
    <span class="meta" id="lastRefresh"></span>
  </div>

  <div id="conn"></div>

  <div class="row"><h3>适配器状态</h3><div class="grow"></div></div>
  <div id="cards"></div>

  <div class="row"><h3>基础组件（Docker 容器）</h3><div class="grow"></div></div>
  <div id="infra"></div>

  <div class="row"><h3>渠道管理（中转站 / 官方 API / 自定义）</h3><div class="grow"></div>
    <button class="ghost" onclick="toggleAdd()">添加渠道</button></div>
  <div id="channels"></div>
  <div id="addch" class="card hidden">
    <div class="row" style="align-items:flex-start">
      <div><div class="meta">名称</div><input id="ch-name" placeholder="如：某某中转站"></div>
      <div style="flex:1;min-width:260px"><div class="meta">Base URL（API 根地址）</div><input id="ch-url" style="width:100%" placeholder="https://api.example.com（不带 /v1/chat/completions）"></div>
    </div>
    <div class="row" style="margin-top:8px;align-items:flex-start">
      <div style="flex:1;min-width:260px"><div class="meta">API Key</div><input id="ch-key" style="width:100%" placeholder="sk-..."></div>
      <div><div class="meta">类型</div>
        <select id="ch-type" style="padding:9px 11px;border-radius:8px;border:1px solid #2c323f;background:#10131a;color:#e6e6e6">
          <option value="1">OpenAI 兼容（多数中转站）</option>
          <option value="14">Anthropic 兼容（Claude 系）</option>
        </select></div>
    </div>
    <div class="row" style="margin-top:8px">
      <span class="meta">用途：</span>
      <label class="meta"><input type="radio" name="chmode" value="pool" checked onchange="chMode()"> 并入 free-chat 轮换（自动调权）</label>
      <label class="meta"><input type="radio" name="chmode" value="backup" onchange="chMode()"> 仅作免费全挂时的备用</label>
      <label class="meta"><input type="radio" name="chmode" value="own" onchange="chMode()"> 独立模型名（客户端点名）</label>
    </div>
    <div class="row" style="margin-top:8px" id="ch-up-row">
      <div><div class="meta">上游真实模型名</div><input id="ch-upstream" placeholder="如 gpt-4o-mini / claude-sonnet-5"></div>
    </div>
    <div class="row hidden" style="margin-top:8px" id="ch-own-row">
      <div style="flex:1"><div class="meta">独立模型名（多个用逗号分隔）</div><input id="ch-own" style="width:100%" placeholder="如 claude-sonnet-5, gpt-4o"></div>
    </div>
    <div class="row" style="margin-top:10px">
      <div class="grow"></div>
      <button class="ghost" onclick="toggleAdd()">取消</button>
      <button onclick="addChannel(this)">保存渠道</button>
    </div>
    <div class="msg" id="ch-msg"></div>
    <div class="hint">如误删内置渠道：适配器地址为 kimi2api:8000 / deeperseeker:4000 / glm2api:8000 / doubao2api:8000（类型 OpenAI 兼容）。新增/修改约 60 秒自动生效，无需重启、无需命令行。</div>
  </div>

  <div class="row"><h3>渠道与自动调权（五家平权 + 按实测速度）</h3><div class="grow"></div></div>
  <div id="scale"></div>

  <div class="row"><h3>用量统计</h3><div class="grow"></div></div>
  <div id="usage"></div>

  <div class="row"><h3>备份（new-api 数据库）</h3><div class="grow"></div>
    <button class="ghost" onclick="runBackup(this)">立即备份</button></div>
  <div id="backup"></div>

  <details id="help">
    <summary>帮助 & 故障速查（点开）</summary>
    <div style="font-size:13px;line-height:1.7;margin-top:10px">
      <b>整个页面都打不开？</b> = Docker Desktop 没启动。从开始菜单启动它，约 1 分钟全部容器自动恢复。<br>
      <b>忘记运维台密码？</b> = 部署机文件 <code>free-adapters/data/ops/.generated-creds.txt</code>，或命令 <code>docker inspect ops-console</code> 看 OPS_ADMIN_PASSWORD。<br>
      <b>某家出现 🔴 凭证过期</b> = 浏览器登录该平台 → 按「更换凭证」下方指引取值 → 粘贴保存（自动重启该适配器）。<br>
      <b>🟡 限流 / ⚪ 积分不足</b> = 等恢复，别连续重试（连续失败可能升级成封号）。<br>
      <b>🟠 适配器报错</b> = 点该行「看日志」；常见是上游协议改动，需要更新适配器代码。<br>
      <b>客户端全部 401</b> = 网关 key 不对；用上面「连接信息」表的 key，体检会核对 key 是否全链路一致。<br>
      <b>响应变慢</b> = 网页逆向固有延迟；自动调权几轮后会压慢家、抬快家。<br>
      <b>生视频报积分不足</b> = GLM 每日额度，等次日，非故障。<br>
      <b>换凭证时 Console 被拦</b> = Chrome/Edge 防粘贴：Console 里手动敲 <code>allow pasting</code> 回车再粘贴；或不用 Console——F12→Application→Local Storage 手抄（DeepSeek 抄 userToken 值里 eyJ 那段、Kimi 抄 refresh_token 整段）；兜底 Network 抓 Authorization。<br>
      详细文档在仓库 docs/（usage / operations / credentials-guide / fresh-machine-setup）。
    </div>
  </details>
</div>
</div>
<div id="modal" class="modal hidden"><div class="box">
  <div class="row"><b id="modalTitle"></b><div class="grow"></div>
    <button class="ghost" onclick="closeModal()">关闭</button></div>
  <div id="modalBody" style="margin-top:10px;overflow:auto;display:flex;flex-direction:column"></div>
</div></div>
<script>
function mask(k){return k==='—'?'—':(k.length<=14?k:(k.slice(0,6)+'••••'+k.slice(-4)));}
function esc(s){return (s??'').toString().replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString():'—';}
function fmtB(n){if(n==null)return '—';if(n>1048576)return (n/1048576).toFixed(1)+' MB';if(n>1024)return (n/1024).toFixed(0)+' KB';return n+' B';}
async function copyValue(t,btn){
  let ok=false;
  try{await navigator.clipboard.writeText(t);ok=true;}catch(e){}
  if(btn){const old=btn.textContent;btn.textContent=ok?'已复制':'复制失败';setTimeout(()=>{btn.textContent=old;},1200);}
}
function copyText(btn){copyValue(btn.dataset.c||'',btn);}
function copyKey(btn){copyValue(btn.previousElementSibling.dataset.full||'',btn);}
async function jget(u){const r=await fetch(u);if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}
async function jpost(u,body){const r=await fetch(u,{method:'POST',body});return r.json();}
function showModal(title,html){document.getElementById('modalTitle').textContent=title;document.getElementById('modalBody').innerHTML=html;document.getElementById('modal').classList.remove('hidden');}
function closeModal(){document.getElementById('modal').classList.add('hidden');}
function login(){
  const value=document.getElementById('pw').value;
  const body=new URLSearchParams({password:value});
  fetch('api/login',{method:'POST',body}).then(r=>{
    if(r.ok){load();}else alert('密码错误');
  });
}
async function load(){
  let d;
  try{d=await jget('api/status');}catch(e){showLogin();return;}
  document.getElementById('login').classList.add('hidden');
  document.getElementById('panel').classList.remove('hidden');
  document.getElementById('lastRefresh').textContent='全部更新于 '+new Date().toLocaleTimeString();
  await Promise.all([loadConn(),renderCards(d),loadInfra(),loadChannels(),loadScale(),loadUsage(),loadBackup()]);
}
function showLogin(){
  document.getElementById('login').classList.remove('hidden');
  document.getElementById('panel').classList.add('hidden');
}
async function loadConn(){
  const d=await jget('api/info');
  const box=document.getElementById('conn');
  let html=`<h3>连接信息（全部端点与 Key）</h3>`;
  html+=`<table>`;
  d.connections.forEach(c=>{
    const keyCell = (c.key==='—')
      ? '<span class="meta">—</span>'
      : '<span class="kv" data-full="'+esc(c.key)+'">'+mask(c.key)+'</span>'
        + '<button class="ghost mini" onclick="copyKey(this)">复制Key</button>'
        + '<button class="ghost mini" onclick="toggleKey(this)">显示</button>';
    html+=`<tr>
      <td style="color:#7dd3fc;width:56px">${c.group}</td>
      <td style="width:170px">${esc(c.name)}</td>
      <td><span style="color:#9aa4b2">${esc(c.url)}</span>
        <button class="ghost mini" data-c="${esc(c.url)}" onclick="copyText(this)">复制</button>
        <div class="hint">${esc(c.note)}</div></td>
      <td style="width:230px">${keyCell}</td></tr>`;
  });
  html+=`</table>`;
  box.innerHTML=html;
}
function toggleKey(btn){
  const s=btn.previousElementSibling.previousElementSibling;
  const showing=s.dataset.show==='1';
  s.textContent=showing?mask(s.dataset.full):s.dataset.full;
  s.dataset.show=showing?'0':'1';
  btn.textContent=showing?'显示':'隐藏';
}
async function renderCards(d){
  const box=document.getElementById('cards');
  box.innerHTML='';
  d.adapters.forEach(a=>{
    const dotClass={ok:'ok',expired:'bad',limited:'warn',quota:'quota',down:'down',error:'err'}[a.state]||'down';
    const stateText={ok:'正常',expired:'凭证过期 · 去换凭证',limited:'限流/风控 · 等一会',quota:'积分/额度不足 · 等恢复',down:'进程未响应',error:'适配器报错 · 看日志'}[a.state]||a.state;
    const card=document.createElement('div');
    card.className='card';
    card.innerHTML=`
      <div class="row">
        <span class="dot ${dotClass}"></span>
        <span class="name">${esc(a.name)}</span>
        <span class="meta">${esc(stateText)}${a.detail&&a.state!=='ok'?' · '+esc(a.detail):''}</span>
        <div class="grow"></div>
        <button class="ghost mini" onclick="showLogs('${a.container}','${esc(a.name)}')">看日志</button>
        <button class="ghost mini" onclick="restart('${a.container}','${esc(a.name)}',this)">重启</button>
        <button class="ghost mini" onclick="toggle('${a.key}',this)">更换凭证</button>
      </div>
      <div class="hidden cred-panel" style="margin-top:12px">
        <textarea id="t-${a.key}" placeholder="粘贴该平台新的登录凭证"></textarea>
        <div class="hint">取法：${esc(a.how)}</div>
        <div class="row" style="margin-top:8px">
          <div class="grow"></div>
          <button class="ghost" onclick="toggle('${a.key}',this)">取消</button>
          <button onclick="save('${a.key}',this)">保存并重启适配器</button>
        </div>
        <div class="msg" id="m-${a.key}"></div>
      </div>`;
    box.appendChild(card);
  });
}
function toggle(key,btn){
  const panel=btn.closest('.card').querySelector('.cred-panel');
  if(panel)panel.classList.toggle('hidden');
}
async function save(key,btn){
  const val=document.getElementById('t-'+key).value;
  const msg=document.getElementById('m-'+key);
  msg.textContent='保存中…';
  btn.disabled=true;
  const body=new URLSearchParams({credential:val});
  try{
    const d=await jpost('api/credential/'+key,body);
    if(d.success){msg.textContent='✅ 已保存，适配器已重启，稍后确认变绿。'+(d.warn?'（提示：'+d.warn+'）':'');}
    else if(d.warning){msg.textContent='⚠️ '+d.warning;}
    else{msg.textContent='❌ '+(d.error||(Array.isArray(d.detail)&&d.detail[0]&&d.detail[0].msg)||'失败');}
  }finally{btn.disabled=false;}
}
async function loadInfra(){
  let d;try{d=await jget('api/infra');}catch(e){return;}
  const box=document.getElementById('infra');
  let html=`<div class="card"><table>
    <tr><th>容器</th><th>作用</th><th>状态</th><th>镜像</th><th>镜像时间</th><th></th></tr>`;
  d.containers.forEach(c=>{
    const st=c.running?`<span class="chk">运行中</span>（${esc(c.uptime||'')}）`:`<span class="nox">未运行</span>`;
    html+=`<tr><td>${esc(c.name)}</td><td class="meta">${esc(c.role)}</td><td>${st}</td>
      <td class="meta">${esc(c.image)}</td><td class="meta">${esc(c.created||'')}</td>
      <td style="text-align:right"><button class="ghost mini" onclick="showLogs('${c.name}','${esc(c.name)}')">看日志</button> ${c.self?'':`<button class="ghost mini" onclick="restart('${c.name}','${esc(c.name)}',this)">重启</button>`}</td></tr>`;
  });
  html+=`</table>
    <div class="meta" style="margin-top:8px">宿主磁盘剩余 <b>${d.disk_free_gb??'?'} GB</b> / 共 ${d.disk_total_gb??'?'} GB${d.docker_images?' · Docker 镜像 '+d.docker_images+' 个共 '+d.docker_images_gb+' GB':''}${(d.disk_free_gb!=null&&d.disk_free_gb<10)?' <span class="nox">⚠️ 剩余不足 10GB，考虑清理旧镜像</span>':''}</div>
  </div>`;
  box.innerHTML=html;
}
async function restart(name,label,btn){
  if(!confirm('重启 '+label+'？'+(name==='new-api'?'（聚合层重启期间网关约 5-10 秒不可用）':'（该容器约 2-5 秒）')))return;
  btn.disabled=true;
  try{
    const d=await jpost('api/restart/'+name);
    alert(d.success?('已重启 '+label):('失败：'+(d.error||'')));
  }finally{btn.disabled=false;setTimeout(load,1500);}
}
async function showLogs(name,label){
  showModal(label+' · 最近日志','<pre id="logbox">加载中…</pre>');
  try{
    const d=await jget('api/logs/'+name+'?lines=80');
    document.getElementById('logbox').textContent=d.lines.join('\\n')||'（无日志）';
  }catch(e){document.getElementById('logbox').textContent='读取失败: '+e.message;}
}
async function loadScale(){
  let d;try{d=await jget('api/autoscale');}catch(e){return;}
  const box=document.getElementById('scale');
  const stMap={1:'正常',2:'手动禁用',3:'自动禁用'};
  let html=`<div class="card">
    <div class="row" style="margin-bottom:8px">
      <span class="meta">${d.enabled?'✅ 已启用':'⛔ 已暂停'} · 每 ${Math.round(d.interval_sec/60)} 分钟一轮 · 窗口 ${d.window_min} 分钟 · 上次 ${fmt(d.last_run)}${d.next_run&&d.enabled?' · 下次 '+fmt(d.next_run):''}${d.note?' · '+esc(d.note):''}</span>
      <div class="grow"></div>
      <button class="ghost mini" onclick="toggleScale(this)">${d.enabled?'暂停':'启用'}</button>
      <button class="ghost mini" onclick="runScale(this)">立即调权一轮</button>
    </div>`;
  if(d.channels&&d.channels.length){
    html+=`<table>
      <tr><th>渠道</th><th>状态</th><th>权重</th><th>窗口成功</th><th>窗口失败</th><th>平均耗时</th><th></th></tr>`;
    d.channels.forEach(c=>{
      const w=(c.old===c.new)?`<b>${c.new}</b>`:`${c.old} → <b style="color:#eab308">${c.new}</b>`;
      const avg=c.avg==null?'—':c.avg+'s';
      const st=c.status===1?'<span class="chk">正常</span>':`<span class="nox">${stMap[c.status]||c.status}</span>`;
      const btn=c.status!==1?`<button class="ghost mini" onclick="enableCh(${c.id},this)">重新启用</button>`:'';
      html+=`<tr><td>${esc(c.name)}</td><td>${st}</td><td>${w}</td><td>${c.n}</td><td>${c.fails||0}</td><td>${avg}</td><td style="text-align:right">${btn}</td></tr>`;
    });
    html+=`</table>`;
  } else {
    html+=`<div class="meta">还没运行过——启动约 20 秒后自动首轮，或点「立即调权一轮」。</div>`;
  }
  if(d.history&&d.history.length){
    html+=`<details style="margin-top:10px"><summary>调整历史（最近 ${d.history.length} 条）</summary><table style="margin-top:6px"><tr><th>时间</th><th>渠道</th><th>变化</th><th>来源</th></tr>`;
    d.history.forEach(h=>{
      html+=`<tr><td class="meta">${fmt(h.ts)}</td><td>${esc(h.name)}</td><td>${h.old} → <b>${h.new}</b></td><td class="meta">${h.why==='manual'?'手动':'自动'}</td></tr>`;
    });
    html+=`</table></details>`;
  }
  html+=`</div>`;
  box.innerHTML=html;
}
async function toggleScale(btn){
  btn.disabled=true;
  try{
    const d=await jget('api/autoscale');
    await jpost('api/autoscale/toggle',new URLSearchParams({enabled:String(!d.enabled)}));
    await loadScale();
  }finally{btn.disabled=false;}
}
async function runScale(btn){
  btn.disabled=true;const old=btn.textContent;btn.textContent='调权中…';
  try{await jpost('api/autoscale/run');await loadScale();}
  finally{btn.disabled=false;btn.textContent=old;}
}
async function enableCh(id,btn){
  btn.disabled=true;
  try{
    const d=await jpost('api/channel/enable/'+id);
    alert(d.success?'已重新启用（约 60 秒生效）':('失败：'+(d.error||'')));
    await loadScale();
  }finally{btn.disabled=false;}
}
async function loadUsage(){
  let d;try{d=await jget('api/usage');}catch(e){return;}
  const box=document.getElementById('usage');
  let html=`<div class="card"><div class="meta" style="margin-bottom:6px">今天（${d.today}）：共 ${d.today_total} 次请求 · 词元 ${d.today_tokens}</div>`;
  if(d.by_channel&&d.by_channel.length){
    html+=`<table><tr><th>渠道</th><th>今天请求</th><th>词元</th><th>平均耗时</th></tr>`;
    d.by_channel.forEach(c=>{
      html+=`<tr><td>${esc(c.name)}</td><td>${c.n}</td><td>${c.tokens}</td><td>${c.avg==null?'—':c.avg+'s'}</td></tr>`;
    });
    html+=`</table>`;
  }else{html+=`<div class="meta">今天还没有请求记录。</div>`;}
  if(d.days&&d.days.length){
    html+=`<div class="meta" style="margin-top:8px">近 7 天：`+d.days.map(x=>`${x.day} <b>${x.n}</b>`).join(' · ')+`</div>`;
  }
  html+=`</div>`;
  box.innerHTML=html;
}
async function loadBackup(){
  let d;try{d=await jget('api/backup');}catch(e){return;}
  const box=document.getElementById('backup');
  let html=`<div class="card"><div class="meta" style="margin-bottom:4px">每日自动备份 1 次（保留最近 ${d.keep} 份，SQLite 在线备份、WAL 安全）——下载即可，不用自己进目录</div><div class="hint" style="margin-bottom:6px">容器内路径 ${esc(d.dir)}（宿主机上就是 new-api 数据目录下的 backups 文件夹）</div>`;
  if(d.items&&d.items.length){
    html+=`<table><tr><th>文件</th><th>大小</th><th>时间</th><th></th></tr>`;
    d.items.slice(0,8).forEach(b=>{
      html+=`<tr><td>${esc(b.name)}</td><td>${fmtB(b.size)}</td><td class="meta">${fmt(b.mtime)}</td>
        <td style="text-align:right"><a href="api/backup/download/${encodeURIComponent(b.name)}">下载</a></td></tr>`;
    });
    html+=`</table>`;
  }else{html+=`<div class="meta">还没有备份（启动约 90 秒后自动首份，或点「立即备份」）。</div>`;}
  html+=`</div>`;
  box.innerHTML=html;
}
async function runBackup(btn){
  btn.disabled=true;const old=btn.textContent;btn.textContent='备份中…';
  try{
    const d=await jpost('api/backup/run');
    alert(d.ok?('✅ 已备份 '+d.file):('❌ '+(d.error||'失败')));
    await loadBackup();
  }finally{btn.disabled=false;btn.textContent=old;}
}
async function doHealth(btn){
  btn.disabled=true;const old=btn.textContent;btn.textContent='体检中…';
  try{
    const d=await jpost('api/healthcheck');
    let html=`<table style="width:100%"><tr><th>检查项</th><th>结果</th><th>详情</th></tr>`;
    d.checks.forEach(c=>{
      const verdict=c.ok?'<span class="chk">✅ 通过</span>':(c.optional?'<span class="meta">⚠️ 未装/未运行（可选，不阻塞）</span>':'<span class="nox">❌ 异常</span>');
      html+=`<tr><td>${esc(c.name)}</td><td>${verdict}</td><td class="meta">${esc(c.detail||'')}</td></tr>`;
    });
    html+=`</table><div class="meta" style="margin-top:8px">体检全程只在本机内部探测，不调用任何模型接口。</div>`;
    showModal('一键体检结果'+(d.all_ok?'：全部通过 ✅':'：有异常 ❌'),html);
  }finally{btn.disabled=false;btn.textContent=old;}
}
async function loadChannels(){
  let d;try{d=await jget('api/channels');}catch(e){return;}
  const box=document.getElementById('channels');
  const stMap={1:'<span class="chk">启用</span>',2:'<span class="nox">停用</span>',3:'<span class="nox">自动禁用</span>'};
  let html=`<div class="card"><table><tr><th>渠道</th><th>用途</th><th>模型</th><th>状态</th><th>今日</th><th></th></tr>`;
  d.channels.forEach(c=>{
    let modelTxt=esc(c.models);
    if(c.mapping){try{const m=JSON.parse(c.mapping);const pairs=Object.entries(m).map(([k,v])=>esc(k)+' ← '+esc(v));if(pairs.length)modelTxt=pairs.join('；');}catch(e){}}
    const badge={ '轮换池':'#4ade80','备用':'#eab308','独立':'#7dd3fc'}[c.place]||'#9aa4b2';
    html+=`<tr id="chrow-${c.id}">
      <td>${esc(c.name)}<div class="hint">${esc(c.base_url)} · ${esc(c.type_name)}</div></td>
      <td><span style="color:${badge}">${c.place}</span>${c.place==='轮换池'?' <span class="meta">w'+c.weight+'</span>':''}</td>
      <td class="meta">${modelTxt}</td>
      <td>${stMap[c.status]||c.status}</td>
      <td>${c.today}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="ghost mini" onclick="chTest(${c.id},this)">测试</button>
        <button class="ghost mini" onclick="chEdit(${c.id})">编辑</button>
        <button class="ghost mini" onclick="chStatus(${c.id},${c.status===1},this)">${c.status===1?'停用':'启用'}</button>
        <button class="ghost mini" data-id="${c.id}" data-name="${esc(c.name)}" onclick="chDel(this)">删除</button>
      </td></tr>
      <tr id="chedit-${c.id}" class="hidden"><td colspan="6">
        <div class="row">
          <input id="ce-name-${c.id}" value="${esc(c.name)}" placeholder="名称">
          <input id="ce-url-${c.id}" value="${esc(c.base_url)}" placeholder="Base URL" style="flex:1;min-width:220px">
        </div>
        <div class="row" style="margin-top:6px">
          <input id="ce-key-${c.id}" placeholder="新 API Key（不改留空）" style="flex:1">
          <button class="ghost" onclick="chEdit(${c.id})">取消</button>
          <button onclick="chSave(${c.id},this)">保存</button>
        </div>
        <div class="msg" id="ce-msg-${c.id}"></div>
      </td></tr>`;
  });
  html+=`</table></div>`;
  box.innerHTML=html;
}
function toggleAdd(){
  const f=document.getElementById('addch');
  f.classList.toggle('hidden');
  if(!f.classList.contains('hidden'))document.getElementById('ch-msg').textContent='';
}
function chMode(){
  const mode=document.querySelector('input[name=chmode]:checked').value;
  document.getElementById('ch-up-row').classList.toggle('hidden',mode==='own');
  document.getElementById('ch-own-row').classList.toggle('hidden',mode!=='own');
}
async function addChannel(btn){
  const mode=document.querySelector('input[name=chmode]:checked').value;
  const params=new URLSearchParams({
    name:document.getElementById('ch-name').value,
    base_url:document.getElementById('ch-url').value,
    key:document.getElementById('ch-key').value,
    mode:mode,
    upstream_model:document.getElementById('ch-upstream').value,
    own_models:document.getElementById('ch-own').value,
    ctype:document.getElementById('ch-type').value,
  });
  const msg=document.getElementById('ch-msg');
  msg.textContent='保存中…';btn.disabled=true;
  try{
    const d=await jpost('api/channels/add',params);
    if(d.success){msg.textContent='✅ '+d.note;['ch-name','ch-url','ch-key','ch-upstream','ch-own'].forEach(i=>document.getElementById(i).value='');await loadChannels();}
    else msg.textContent='❌ '+(d.error||(Array.isArray(d.detail)&&d.detail[0]&&d.detail[0].msg)||'失败');
  }finally{btn.disabled=false;}
}
function chEdit(id){document.getElementById('chedit-'+id).classList.toggle('hidden');}
async function chSave(id,btn){
  const msg=document.getElementById('ce-msg-'+id);
  btn.disabled=true;msg.textContent='保存中…';
  const params=new URLSearchParams({
    name:document.getElementById('ce-name-'+id).value,
    base_url:document.getElementById('ce-url-'+id).value,
    key:document.getElementById('ce-key-'+id).value,
  });
  try{
    const d=await jpost('api/channels/update/'+id,params);
    msg.textContent=d.success?('✅ '+d.note):('❌ '+(d.error||'失败'));
    if(d.success)await loadChannels();
  }finally{btn.disabled=false;}
}
async function chStatus(id,isOn,btn){
  btn.disabled=true;
  const params=new URLSearchParams({enabled:String(!isOn)});
  try{
    const d=await jpost('api/channels/set_status/'+id,params);
    if(!d.success)alert('失败：'+(d.error||'未知错误'));
    await loadChannels();
  }finally{btn.disabled=false;}
}
async function chDel(btn){
  const id=btn.dataset.id,name=btn.dataset.name;
  if(!confirm('删除渠道「'+name+'」？删除后其请求将改走其他渠道（约 60 秒生效）。'))return;
  const d=await jpost('api/channels/delete/'+id);
  alert(d.success?('已删除：'+d.deleted):('失败：'+(d.error||'')));
  await loadChannels();
}
async function chTest(id,btn){
  btn.disabled=true;const old=btn.textContent;btn.textContent='…';
  try{
    const d=await jpost('api/channels/test/'+id);
    alert((d.ok?'✅ ':'❌ ')+(d.detail||d.error||'未知错误'));
  }finally{btn.disabled=false;btn.textContent=old;}
}
setInterval(async ()=>{
  if(document.getElementById('panel').classList.contains('hidden'))return;
  if(document.hidden)return;
  if(!document.getElementById('auto').checked)return;
  if(document.querySelector('#cards .cred-panel:not(.hidden)'))return;
  const openTA=[...document.querySelectorAll('textarea')].some(t=>t.closest('.hidden')===null&&t.value.trim()!=='');
  if(openTA)return;
  try{
    const d=await jget('api/status');
    await Promise.all([renderCards(d),loadInfra(),loadScale()]);
    document.getElementById('lastRefresh').textContent='状态区更新于 '+new Date().toLocaleTimeString();
  }catch(e){}
},30000);
load().catch(()=>showLogin());
</script></body></html>"""
