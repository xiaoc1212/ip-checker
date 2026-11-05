const $=id=>document.getElementById(id);
let current=null,pending=false,checking=false,observing=false,launching=false,version=null;
const labels={pass:'通过',fail:'未通过',unknown:'待核验',info:'参考',expired:'已过期'};
async function api(route,body){const r=await fetch('./'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw Error(data.error||'请求未完成');return data}
function badge(state){const node=document.createElement('span');node.className='badge '+state;node.textContent=labels[state]||'待核验';return node}
function row(host,title,detail,state){const line=document.createElement('div');line.className='row';const name=document.createElement('div');name.textContent=title;const content=document.createElement('div');content.textContent=detail||'未确认';if(state)content.append(badge(state));line.append(name,content);host.append(line)}
function render(s){
 if(version&&s.asset_version!==version){pending=false;location.reload();return}version=s.asset_version;current=s;
 if(document.activeElement!==$('expected'))$('expected').value=s.expected;
 $('start').disabled=s.busy||checking||launching;$('check').disabled=s.busy||checking||launching;
 const c=s.report?.checks||s.partial||{};const failed=(s.required||[]).filter(x=>x.state!=='pass');
 $('verdict').textContent=s.busy?(pending?'正在检测，通过后启动 Claude…':'正在检测客户端环境…'):s.ready?'连接预检通过':s.report?'暂不启动：客户端预检尚未通过':'等待客户端预检';
 $('hero').className='panel hero '+(s.ready?'pass':s.report?'fail':'');
 $('reason').textContent=s.error||(!s.report?'只读检查，不修改现有网络配置。':s.ready?'可以启动后复核实际连接；断线阻断与登录验收仍以专项证据为准。':'未通过项目：'+failed.map(x=>x.label+'（'+labels[x.state]+'）').join('、'));
 $('checks').replaceChildren();for(const item of s.required||[]){const node=document.createElement('div');node.className='check';node.textContent=item.label;node.append(badge(item.state));$('checks').append(node)}
 $('preflight').replaceChildren();for(const [key,title] of [['client','客户端安装'],['claude','经本地代理访问 Claude'],['system','经系统路径访问 Claude'],['guard','客户端规则与优先级']]){const item=c[key];row($('preflight'),title,item?[item.value,item.detail].filter(Boolean).join(' · '):'等待检测',item?.state||'unknown')}
 $('evidence').replaceChildren();const evidence=s.report?.client_evidence;if(evidence?.length){for(const item of evidence)row($('evidence'),item.label,item.detail,item.state)}else row($('evidence'),'实时配置','尚未取得客户端规则证据','unknown');
 $('time').textContent=s.report?'本次检测完成于 '+new Date(s.report.finished*1000).toLocaleTimeString()+' · 启动结果有效期 60 秒':'';
 if(pending&&!s.busy&&s.report){pending=false;if(s.ready)launch();}
}
async function check(andLaunch){if(checking||current?.busy||launching)return;checking=true;pending=andLaunch;$('start').disabled=true;$('check').disabled=true;try{await api('check',{expected:$('expected').value.trim()});await poll()}catch(e){pending=false;$('verdict').textContent=e.message}finally{checking=false;if(current)render(current)}}
async function launch(){if(launching)return;launching=true;try{await api('launch',{});$('verdict').textContent='已发出启动请求，正在观察客户端连接';await observe()}catch(e){$('verdict').textContent=e.message}finally{launching=false}}
async function poll(){try{render(await api('state'))}catch{pending=false;current=null;$('verdict').textContent='检测服务暂不可用，已取消自动启动';$('start').disabled=true;$('check').disabled=true}}
async function observe(){if(observing)return;observing=true;try{const data=await api('connections');$('connections').replaceChildren();if(data.error||!data.connections?.length){$('observation').textContent=data.error||'未观察到客户端活跃连接，尚不能完成启动后复核';return}const wrong=data.connections.some(x=>x.state==='fail');const external=data.connections.some(x=>x.state==='pass');$('observation').textContent=wrong?'注意：发现未命中预期住宅组的客户端连接。本工具仅提示，没有强制断网。':external?'本次观察到的客户端外部连接均命中预期住宅组':'仅观察到本地连接，外部路径尚未确认';for(const item of data.connections){const p=document.createElement('p');p.textContent=[item.process,item.host,item.chains.join(' → '),item.state==='pass'?'住宅组已命中':item.state==='local'?'本地连接':'路径待处理'].join(' · ');$('connections').append(p)}}catch{$('observation').textContent='客户端连接观察暂不可用'}finally{observing=false}}
$('start').onclick=()=>check(true);$('check').onclick=()=>check(false);poll();observe();setInterval(poll,1500);setInterval(observe,5000);
