/* The UI reports real backend progress. It never grants launch permission. */
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const stepDefinitions = [
  {id:'guard',title:'第 1 关：Clash 代理软件开了吗？',idle:'检查 Clash 接管、Claude 专用规则，以及客户端是否已安装'},
  {id:'exit',title:'第 2 关：静态住宅 IP 走对了吗？',idle:'核对客户端需要的固定出口是否与你保存的目标 IP 一致'},
  {id:'dns',title:'第 3 关：真实位置藏好了吗？（DNS 参考）',idle:'DNS 是否泄漏需要独立采样；仅靠域名解析不能得出安全结论'},
  {id:'tls',title:'第 4 关：Claude 官方服务连得通吗？',idle:'验证官方 HTTPS 证书、连接和出口一致性'}
];
const stateNames = {pass:'已通过', fail:'未通过', unknown:'待核验', pending:'等待检查', running:'正在检查', info:'参考', warn:'需留意'};
let current = null;
let version = null;
let requestBusy = false;
let launchBusy = false;
let pollBusy = false;
let observeBusy = false;
let targetDirty = false;
let lastLaunchRun = null;
let actionError = '';
let serviceOnline = false;
let toastTimer;
let expandedFailures = new Set();
let connectionSignature = '';

function node(tag, className, text){
  const result=document.createElement(tag);
  if(className)result.className=className;
  if(text!==undefined)result.textContent=text;
  return result;
}
function icon(name){
  const svg=document.createElementNS(NS,'svg');svg.setAttribute('class','icon');svg.setAttribute('aria-hidden','true');
  const use=document.createElementNS(NS,'use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;
}
function badge(state, text){return node('span','badge '+state,text||stateNames[state]||'待核验')}
function row(host,title,detail,state){
  const line=node('div','row');const content=node('div','',detail||'未确认');content.append(badge(state));
  line.append(node('div','',title),content);host.append(line);
}
function notify(message){
  $('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);
  toastTimer=setTimeout(()=>$('toast').hidden=true,5000);
}
async function api(route,body){
  const response=await fetch('./'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw Error(result.error||'请求未完成，请重试');return result;
}
function openAdvanced(){
  $('advanced').open=true;$('advanced').scrollIntoView({behavior:'smooth',block:'start'});
}
function initializeSteps(){
  for(const [index,step] of stepDefinitions.entries()){
    const li=node('li','step pending');li.id='step-'+step.id;
    const symbol=node('span','step-symbol',String(index+1).padStart(2,'0'));symbol.id='symbol-'+step.id;
    const body=node('div','step-body');body.append(node('h3','step-title',step.title));
    const desc=node('p','step-desc',step.idle);desc.id='desc-'+step.id;body.append(desc);
    const details=node('details','step-detail');details.id='detail-'+step.id;details.hidden=true;
    details.append(node('summary','','查看检测依据'));const evidence=node('p');evidence.id='proof-'+step.id;details.append(evidence);body.append(details);
    const result=node('span','step-result');result.id='result-'+step.id;result.append(node('span','status-lamp'),node('span','','等待检查'));
    li.append(symbol,body,result);$('steps').append(li);
  }
}
function singleState(check, installed=false){
  if(!check)return null;
  if(installed)return check.value==='已安装'?'pass':'unknown';
  return ['pass','fail','unknown'].includes(check.state)?check.state:'unknown';
}
function stepView(definition,s){
  const checks=s.report?.checks||s.partial||{}, running=s.partial?.progress?.step;
  const step=definition.id, risk=s.dns_audit;
  let status='pending', summary=definition.idle, detail='';
  if(step==='guard'){
    const client=checks.client, guard=checks.guard;
    const clientState=singleState(client,true);
    if(client?.value&&clientState!=='pass')status='fail';
    else if(guard?.state==='fail')status='fail';
    else if(clientState==='pass'&&guard?.state==='pass')status='pass';
    else if(s.busy&&['client','guard'].includes(running))status='running';
    else if(s.report||s.error)status='unknown';
    summary=status==='pass'?'🟢 代理接管、规则和 Claude 客户端均已确认':
      status==='fail'?'🔴 代理、专用规则或客户端存在问题，请检查':
      status==='running'?'正在核对 Clash、客户端与专用规则…':definition.idle;
    detail=['客户端：'+(client?.detail||client?.value||'待确认'),'代理和规则：'+(guard?.detail||guard?.value||'待确认')].join('；');
  }else if(step==='exit'){
    const a=checks.claude,b=checks.system,expected=s.expected||'未配置';
    if(a&&b){status=a.state==='fail'||b.state==='fail'?'fail':a.state==='pass'&&b.state==='pass'?'pass':'unknown'}
    else status=s.busy&&running==='exit'?'running':s.report||s.error?'unknown':'pending';
    const actual=[a?.ip,b?.ip].filter(Boolean).join(' / ');
    summary=status==='pass'?'🟢 两条受检路径均匹配 '+expected:
      status==='fail'?'🔴 出口不匹配（已测：'+(actual||'未取得')+'；目标：'+expected+'）':
      status==='running'?'正在实测 Claude 网络与系统路径的出口…':'目标 IP：'+expected+'；尚未得到两条真实结果';
    detail=['代理路径：'+(a?.detail||a?.value||'待确认'),'系统路径：'+(b?.detail||b?.value||'待确认')].join('；');
  }else if(step==='dns'){
    // Read-only advisory. The ordinary DNS probe does not establish absence of leaks.
    status='unknown';
    const verdict=risk?.capture_verdict||{},text=String(verdict.value||'')+' '+String(verdict.detail||'');
    const observed=verdict.state==='fail'||/观察到.*(?:DNS|明文|查询)|泄漏|外发|出网尝试/.test(text)&&!/未观察到/.test(text);
    if(observed)status='fail';
    summary=observed?'🔴 专项采样提示可能的明文 DNS 出网，请看详细证据':
      risk?'🟡 仅完成一次有限范围采样，不能证明以后不会泄漏':
      '🟡 尚未执行独立 DNS 采样；不把普通解析当作无泄漏';
    detail='独立 DNS 参考项：'+(verdict.detail||verdict.value||'需在技术排查详情执行专项采样')+'；此项不冒充后台四项必检绿灯。';
  }else{
    const check=checks.tls;
    status=singleState(check)|| (s.busy&&running==='tls'?'running':s.report||s.error?'unknown':'pending');
    summary=status==='pass'?'🟢 官方 HTTPS 证书及目标出口核验通过':
      status==='fail'?'🔴 官方服务握手失败，或证书/出口不符合预期':
      status==='running'?'正在验证官方服务证书及出口…':definition.idle;
    detail=[check?.value,check?.detail].filter(Boolean).join(' · ');
  }
  return {state:status,summary,detail};
}
function renderSteps(s){
  const checks=s.report?.checks||s.partial||{};
  let passed=0,hasFail=false,hasUnknown=false;
  for(const def of stepDefinitions){
    const view=stepView(def,s),status=view.state;
    if(status==='fail')hasFail=true;if(status==='unknown')hasUnknown=true;
    const item=$('step-'+def.id);item.className='step '+status;
    $('desc-'+def.id).textContent=view.summary;
    item.title=def.title+'：'+view.summary+(view.detail?'\n'+view.detail:'');
    const symbol=$('symbol-'+def.id);symbol.replaceChildren();
    if(status==='running')symbol.append(node('span','spinner'));
    else if(status==='pending')symbol.textContent=String(stepDefinitions.indexOf(def)+1);
    else symbol.append(icon(status==='pass'?'check':status==='fail'?'close':'alert'));
    $('result-'+def.id).replaceChildren(node('span','status-lamp'),node('span','',status==='unknown'?'待核验':stateNames[status]));
    const details=$('detail-'+def.id);details.hidden=!view.detail;$('proof-'+def.id).textContent=view.detail;
    // Do not auto-expand technical paragraphs into the main four-row view.
  }
  const mandatory=[
    singleState(checks.client,true),singleState(checks.guard),
    checks.claude&&checks.system?(checks.claude.state==='pass'&&checks.system.state==='pass'?'pass':checks.claude.state==='fail'||checks.system.state==='fail'?'fail':'unknown'):null,
    singleState(checks.tls)
  ];
  const complete=mandatory.filter(Boolean).length;
  passed=mandatory.filter(x=>x==='pass').length;
  $('progressCount').textContent=complete+' / 4';
  $('progressFill').style.width=complete/4*100+'%';
  $('progressBar').setAttribute('aria-valuenow',String(complete));
  $('progressFill').style.background=mandatory.includes('fail')?'var(--red)':mandatory.includes('unknown')?'var(--amber)':'var(--green)';
  $('progressText').textContent=s.busy?'正在按真实检测进度逐项核对':s.report?'必检 '+passed+' / 4 通过（DNS 独立参考）':'等待开始体检';
  $('runBadge').className='run-badge '+(s.busy?'running':s.ready?'pass':mandatory.includes('fail')?'fail':'');
  $('runBadge').textContent=s.busy?'正在体检':s.ready?'必检通过':s.report?'需处理':'尚未开始';
  return {complete,passed,hasFail,hasUnknown,dnsAdvisory:stepView(stepDefinitions[2],s).state};
}
function renderAdvanced(s){
  const checks=s.report?.checks||s.partial||{};
  $('diagnostics').replaceChildren();
  for(const [key,label] of [['dns','系统域名解析'],['ipv6','系统 IPv6 路径'],['tls','证书与服务响应']]){
    const check=checks[key];row($('diagnostics'),label,check?[check.value,check.detail].filter(Boolean).join(' · '):'尚未检查',check?.state||'unknown');
  }
  $('environment').replaceChildren();for(const entry of s.report?.environment||[])row($('environment'),entry.label,[entry.value,entry.detail].filter(Boolean).join(' · '),entry.state);
  $('evidence').replaceChildren();for(const entry of s.report?.client_evidence||[])row($('evidence'),entry.label,entry.detail,entry.state);
  if(!s.report?.client_evidence?.length)row($('evidence'),'规则证据','检查后显示，当前不作通过判断','unknown');
  const audit=s.dns_audit;$('dnsAudit').replaceChildren();
  if(audit){
    const capture=audit.capture||{},verdict=audit.capture_verdict||{};
    $('dnsAuditTime').textContent='上次连接核对：'+new Date(audit.checked*1000).toLocaleString()+(capture.finished?' · 采样结束：'+new Date(capture.finished*1000).toLocaleString():'');
    row($('dnsAudit'),'客户端域名路径',audit.path_statement,'info');
    row($('dnsAudit'),'明文 DNS 采样',[verdict.value,verdict.detail].filter(Boolean).join(' · '),verdict.state||'unknown');
    row($('dnsAudit'),'默认 DNS 身份验证',audit.configuration_statement,audit.dns_certificate_warning?'warn':'info');
  }else{$('dnsAuditTime').textContent='尚未执行专项采样';row($('dnsAudit'),'采样结果','尚无独立采样，不能由解析成功推断没有泄露','unknown')}
  $('dnsAuditJob').textContent=s.dns_audit_job?.detail||'';
  const messages=[];
  if(audit?.dns_certificate_warning)messages.push('默认 DNS 跳过证书校验');
  if(s.report?.checks?.ipv6?.state==='unknown')messages.push('IPv6 旁路尚未确认');
  messages.push('断线阻断尚未部署');
  $('attentionTitle').textContent=messages.length+' 项需要留意';$('attentionText').textContent=messages.join(' · ')+'。详情可展开查看。';
}
function render(s){
  if(version&&s.asset_version!==version){location.reload();return}version=s.asset_version||version;
  if(current?.run_id!==s.run_id)expandedFailures=new Set();current=s;serviceOnline=true;
  $('serviceDot').className='dot online';$('serviceText').textContent='检测服务在线';
  if(document.activeElement!==$('expected')&&!targetDirty)$('expected').value=s.expected;
  const sampling=Boolean(s.dns_audit_job?.busy),locked=s.busy||requestBusy||launchBusy||sampling;
  $('expected').disabled=locked;$('editTarget').disabled=locked;$('check').disabled=locked;$('dnsAuditStart').disabled=locked;
  $('clientVersion').textContent=(s.report?.checks||s.partial||{}).client?.detail||'检查后识别客户端版本';
  const result=renderSteps(s);renderAdvanced(s);
  const expired=s.required?.some(row=>row.state==='expired');
  const runtimeBad=s.monitor?.state==='fail'&&Date.now()/1000-s.monitor.checked<40;
  const changed=targetDirty||$('expected').value.trim()!==s.expected;
  const allow=s.ready&&!locked&&!runtimeBad&&!changed;
  $('launch').disabled=!allow;document.querySelector('.launch-card').classList.toggle('ready',allow);
  let title='等待环境检查',reason='完成左侧检查后，启动按钮将解锁。',lamp='pending',hint='不会自动打开客户端，由你决定何时启动。';
  if(sampling){title='DNS 专项采样中';reason='采样结束后，可以继续启动前检查。';lamp='unknown'}
  else if(s.busy){title='正在逐项检查';reason=s.partial?.progress?.label||'正在读取本机环境…';lamp='unknown'}
  else if(actionError){title='操作未完成';reason=actionError;lamp='unknown'}
  else if(changed){title='目标出口已修改';reason='请重新检查新的固定出口。';lamp='unknown'}
  else if(runtimeBad){title='运行状态需要留意';reason='观察到规则或连接异常，请查看连接记录。';lamp='fail'}
  else if(lastLaunchRun===s.run_id){title='已发送启动请求';reason='客户端实际连接由下方持续观察。';lamp='pass';hint=expired?'上次预检已过期；再次打开前需重检。':'使用中的连接状态见下方。'}
  else if(expired){title='结果已过期，请重检';reason='仅影响再次启动，不表示当前连接已异常。';lamp='unknown'}
  else if(s.ready){title='连接预检已通过';reason='4 项必检已完成，可以手动启动。';lamp='pass';hint='启动后继续核对客户端实际连接。'}
  else if(s.report||s.error){title=result.hasFail?'有检查项未通过':'还有项目待核验';reason=s.error||'查看未通过项目的原因，处理后重新检查。';lamp=result.hasFail?'fail':'unknown'}
  if(!s.report&&!s.busy&&!sampling&&!changed&&!actionError)title='启动 Claude 前，请先进行环境安全体检';
  else if(allow){title='✅ 启动必检全部通过，可以手动启动 Claude';reason='固定出口与 TLS 已验证；DNS 仍属独立参考，不能保证账号零风险。';}
  else if(result.hasFail&&!s.busy){title='❌ 发现网络或检查异常，已暂停启动';reason='请核对失败项目，不要在出口不确定时直接启动。';}
  $('verdict').textContent=title;$('reason').textContent=reason;$('overallLamp').className='status-lamp '+lamp;
  $('examBanner').className='claude-exam-banner '+(s.busy?'running':allow?'pass':lamp==='fail'?'fail':'pending');
  $('examShield').textContent=s.busy?'🛡':allow?'✅':lamp==='fail'?'❗':'🛡';
  $('checkLabel').textContent=s.busy?'🔎 正在逐项体检…':s.report?'🔍 重新体检':'🔍 开始逐项体检';
  $('launchLabel').textContent=launchBusy?'🚀 正在启动…':allow?'🚀 体检通过 · 立即启动 Claude':expired?'🔒 已过期 · 重新体检':'🔒 必检通过后启动 Claude';
  $('launchHint').textContent=hint;
  window.dispatchEvent(new CustomEvent('claude-state',{detail:{ready:allow,busy:s.busy,sampling,report:Boolean(s.report),expired,failed:result.hasFail||runtimeBad,changed,error:Boolean(s.error)}}));
  $('time').textContent=s.busy?'检查用时 '+Math.max(0,Math.floor(Date.now()/1000-(s.started||Date.now()/1000)))+' 秒':s.report?'用时 '+Math.max(1,Math.round(s.report.finished-s.report.started))+' 秒 · '+new Date(s.report.finished*1000).toLocaleTimeString():'尚未检查';
}
async function poll(){
  if(pollBusy)return;pollBusy=true;
  try{render(await api('state'))}catch{
    window.dispatchEvent(new CustomEvent('claude-state',{detail:{offline:true}}));
    serviceOnline=false;$('serviceDot').className='dot';$('serviceText').textContent='检测服务离线';
    $('verdict').textContent='暂时无法连接检测服务';$('reason').textContent='请重新打开启动台；当前不会启动客户端。';
    $('overallLamp').className='status-lamp unknown';$('launch').disabled=true;$('check').disabled=true;$('dnsAuditStart').disabled=true;
  }finally{pollBusy=false}
}
async function check(){
  if(!serviceOnline||requestBusy||current?.busy||current?.dns_audit_job?.busy||launchBusy)return;
  requestBusy=true;actionError='';lastLaunchRun=null;$('check').disabled=true;$('launch').disabled=true;
  $('expected').readOnly=true;$('editTarget').textContent='修改';
  try{const expected=$('expected').value.trim();const started=await api('check',{expected});targetDirty=false;current={...current,expected,busy:true,run_id:started.run_id,report:null,partial:{progress:{step:'client',label:'识别本机客户端'}}};render(current);await poll()}
  catch(error){actionError=error.message;notify(error.message)}
  finally{requestBusy=false;if(current&&serviceOnline)render(current)}
}
async function launch(){
  if(!serviceOnline||!current?.ready||$('launch').disabled||launchBusy)return;
  launchBusy=true;actionError='';const selected={run_id:current.run_id,expected:current.expected};render(current);
  try{await api('launch',selected);lastLaunchRun=selected.run_id;notify('已发送 Claude 启动请求');await observe()}
  catch(error){actionError=error.message;notify(error.message)}
  finally{launchBusy=false;await poll()}
}
async function observe(){
  if(observeBusy)return;observeBusy=true;
  try{
    const data=await api('connections');const age=Date.now()/1000-(data.checked||0);const stale=age>40;
    const connections=data.connections||[],wrong=connections.some(item=>item.state==='fail');
    const external=connections.filter(item=>item.state==='pass').length;
    const state=stale||data.error||data.state==='unknown'?'unknown':wrong||data.state==='fail'?'fail':external?'pass':'pending';
    const label={unknown:'状态待确认',fail:'需要留意',pass:'住宅组已命中',pending:'暂无活跃连接'}[state];
    $('liveStatus').replaceChildren(node('span','status-lamp '+state),node('span','',label));
    $('observation').textContent=state==='unknown'?'连接观察暂时未取得有效结果。':wrong?'发现客户端连接未命中预期住宅组。此处仅告警，不会强制断网。':data.state==='fail'?'运行规则发生变化，请重新检查。':external?external+' 条客户端外部连接命中住宅组，正在持续观察。':'尚未观察到客户端活跃连接；启动后会继续核对。';
    $('monitor').textContent=(data.checked?'观察时间 '+new Date(data.checked*1000).toLocaleTimeString()+' · ':'')+(data.detail||'未取得配置状态');
    $('connectionCount').textContent=connections.length+' 条';
    const signature=JSON.stringify(connections.map(item=>[item.id,item.host,item.state,item.chains]));
    if(signature!==connectionSignature){
      connectionSignature=signature;$('connections').replaceChildren();
      if(!connections.length)$('connections').append(node('p','fine-print','暂时没有可展示的连接。'));
      for(const item of connections){const line=node('div','connection-row');const content=node('span','',item.host||'未知目标');content.append(node('small','',item.process+' · '+item.chains.join(' → ')));line.append(content,badge(item.state==='local'?'info':item.state,item.state==='pass'?'已命中':item.state==='local'?'本地连接':'路径异常'));$('connections').append(line)}
    }
  }catch{$('liveStatus').textContent='观察服务暂不可用';$('observation').textContent='未取得客户端实际连接，不能判为通过。'}
  finally{observeBusy=false}
}
$('check').onclick=check;$('launch').onclick=launch;
$('editTarget').onclick=()=>{const editing=$('expected').readOnly;$('expected').readOnly=!editing;$('editTarget').textContent=editing?'完成':'修改';if(editing){$('expected').focus();$('expected').select()}else $('expected').blur()};
$('expected').oninput=()=>{targetDirty=!current||$('expected').value.trim()!==current.expected;if(current)render(current)};
$('navAdvanced').onclick=openAdvanced;$('attention').onclick=openAdvanced;
$('dnsAuditStart').onclick=async()=>{try{actionError='';$('dnsAuditStart').disabled=true;await api('dns-audit',{});notify('请确认 Windows 权限提示，采样将自动结束');await poll()}catch(error){notify(error.message)}};
$('quitAdvanced').onclick=$('quit').onclick=async()=>{try{await api('quit',{});notify('检测服务已退出，后台观察已停止')}catch(error){notify(error.message)}};
async function refreshLoop(){await poll();setTimeout(refreshLoop,current?.busy?250:1500)}
// Rem-based sizing preserves normal browser zoom and lets layout wrap at any size.
let uiScale=1;
function setUIScale(value){
  uiScale=Math.min(1.4,Math.max(.85,Math.round(value*100)/100));
  document.documentElement.style.setProperty('--ui-scale',String(uiScale));
  $('scaleReset').textContent=uiScale===1?'自动':Math.round(uiScale*100)+'%';
  $('scaleReset').setAttribute('aria-label','当前界面大小 '+Math.round(uiScale*100)+'%，点击恢复自动适应窗口');
  $('scaleDown').disabled=uiScale<=.85;$('scaleUp').disabled=uiScale>=1.4;
  try{localStorage.setItem('ipdesk-ui-scale',String(uiScale))}catch{}
}
try{const saved=Number(localStorage.getItem('ipdesk-ui-scale'));if(Number.isFinite(saved)&&saved>=.85&&saved<=1.4)uiScale=saved}catch{}
$('scaleDown').onclick=()=>setUIScale(uiScale-.1);
$('scaleUp').onclick=()=>setUIScale(uiScale+.1);
$('scaleReset').onclick=()=>setUIScale(1);
setUIScale(uiScale);
initializeSteps();refreshLoop();observe();setInterval(observe,5000);
