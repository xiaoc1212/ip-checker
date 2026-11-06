/* The UI reports real backend progress. It never grants launch permission. */
const $ = id => document.getElementById(id);
const NS = 'http://www.w3.org/2000/svg';
const stepDefinitions = [
  {id:'client', title:'识别 Claude 客户端', idle:'确认本机安装了可启动的客户端'},
  {id:'guard', title:'检查代理与专用规则', idle:'核对接管状态、进程规则与 UDP 规则'},
  {id:'exit', title:'核对固定出口', idle:'分别测试本地代理和系统网络路径'},
  {id:'tls', title:'验证证书与连接', idle:'确认连接证书有效，服务响应正常'}
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
  const checks=s.report?.checks||s.partial||{};
  const running=s.partial?.progress?.step;
  let state,detail='',summary='';
  if(definition.id==='exit'){
    const a=checks.claude,b=checks.system;
    if(a&&b){state=[a.state,b.state].includes('fail')?'fail':a.state==='pass'&&b.state==='pass'?'pass':'unknown';
      detail=['本地代理：'+[a.value,a.detail].filter(Boolean).join(' · '),'系统路径：'+[b.value,b.detail].filter(Boolean).join(' · ')].join('；');
      summary=state==='pass'?'两条路径均匹配 '+(a.ip||'目标出口'):state==='fail'?'实际出口与目标不一致':'部分路径没有取得有效结果';
    }
  }else{
    const check=checks[definition.id];state=singleState(check,definition.id==='client');
    if(check){detail=[check.value,check.detail].filter(Boolean).join(' · ');
      summary=state==='pass'?({client:check.detail||'已找到客户端',guard:'接管、进程与 UDP 规则已核对',tls:'证书验证通过，连接出口一致'}[definition.id]):check.value||'尚未取得有效结果';}
  }
  if(!state){
    state=s.busy&&running===definition.id?'running':s.report||s.error?'unknown':'pending';
    summary=state==='running'?({client:'正在识别安装位置与版本…',guard:'正在读取运行中的代理规则…',exit:'正在向 Claude 实测两条出口路径…',tls:'正在验证证书与服务响应…'}[definition.id]):state==='unknown'?'本次未取得完整证据':definition.idle;
  }
  return {state,summary,detail};
}
function renderSteps(s){
  let complete=0,passed=0,hasFail=false,hasUnknown=false;
  for(const definition of stepDefinitions){
    const view=stepView(definition,s);const {state}=view;
    if(['pass','fail','unknown'].includes(state))complete++;
    if(state==='pass')passed++;if(state==='fail')hasFail=true;if(state==='unknown')hasUnknown=true;
    $('step-'+definition.id).className='step '+state;
    $('desc-'+definition.id).textContent=view.summary;
    $('step-'+definition.id).title=definition.title+'：'+view.summary+(view.detail?'\n'+view.detail:'');
    const symbol=$('symbol-'+definition.id);symbol.replaceChildren();
    if(state==='running')symbol.append(node('span','spinner'));
    else if(state==='pending')symbol.textContent=String(stepDefinitions.indexOf(definition)+1).padStart(2,'0');
    else symbol.append(icon(state==='pass'?'check':state==='fail'?'close':'alert'));
    $('result-'+definition.id).replaceChildren(node('span','status-lamp'),node('span','',stateNames[state]));
    const details=$('detail-'+definition.id);details.hidden=!view.detail;$('proof-'+definition.id).textContent=view.detail;
    const failureKey=s.run_id+':'+definition.id;
    if(['fail','unknown'].includes(state)&&view.detail&&!expandedFailures.has(failureKey)){
      if(state==='fail')details.open=true;expandedFailures.add(failureKey);
    }
  }
  $('progressCount').textContent=complete+' / 4';$('progressFill').style.width=(complete/4*100)+'%';
  $('progressBar').setAttribute('aria-valuenow',String(complete));$('progressFill').style.background=hasFail?'var(--red)':hasUnknown?'var(--amber)':'var(--green)';
  $('progressText').textContent=s.busy?'正在逐项检查，请稍候':complete===4?'检查已完成 · '+passed+' 项通过':complete?'已取得 '+complete+' 项结果':'等待开始检查';
  const display=s.busy?['running','检测中']:hasFail?['fail','需要处理']:hasUnknown?['running','仍需核验']:passed===4?['pass','检查完成']:['','尚未开始'];
  $('runBadge').className='run-badge '+display[0];$('runBadge').textContent=display[1];
  return {complete,passed,hasFail,hasUnknown};
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
  $('verdict').textContent=title;$('reason').textContent=reason;$('overallLamp').className='status-lamp '+lamp;
  $('checkLabel').textContent=s.busy?'检查中…':s.report?'重新检查':'开始环境检查';
  $('launchLabel').textContent=launchBusy?'正在打开…':allow?'启动 Claude':expired?'重检后可启动':'检查通过后启动';
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
