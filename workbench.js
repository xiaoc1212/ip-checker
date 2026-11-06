/* The workbench manages saved IDs; only the existing Claude UI can request a protected launch. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const el = (tag, cls, text) => { const result=document.createElement(tag);if(cls)result.className=cls;if(text!==undefined)result.textContent=text;return result; };
  const state={items:[],groups:[],subscriptions:[],group:'all',editing:null,editingSub:null,missing:null,drag:null,opening:new Set(),mutation:false,refreshing:false};
  const collator=new Intl.Collator('zh-CN-u-co-pinyin');
  const initials=[...['a阿','b芭','c擦','d搭','e蛾','f发','g噶','h哈','j击','k喀','l垃','m妈','n拿','o哦','p啪','q期','r然','s撒','t塌','w挖','x昔','y压','z匝']].map(x=>[x[0],x[1]]);
  const compact=value=>value.toLowerCase().replace(/[\s&·/（）()_-]/g,'');
  function searchKeys(name){
    const pinyin=[...name].map(char=>{
      if(!/[\u3400-\u9fff]/u.test(char))return char;
      return [...initials].reverse().find(([,first])=>collator.compare(char,first)>=0)?.[0]||char;
    }).join('');
    const english=name.match(/[A-Za-z]+/g)||[];
    return [compact(name),compact(pinyin),english.map(word=>word[0].toLowerCase()).join('')];
  }
  function matches(item){const q=compact(byId('launcherSearch').value.trim());return !q||searchKeys(item.name).some(key=>key.includes(q));}
  let toastTimer;
  function toast(message){const box=byId('workbenchToast');box.textContent=message;box.showPopover();clearTimeout(toastTimer);toastTimer=setTimeout(()=>box.hidePopover(),4200);}
  async function request(route,body){
    const response=await fetch('./api/'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result=await response.json();if(!response.ok){const error=Error(result.error||'操作未完成，请重试');error.data=result;throw error;}return result;
  }
  function showDialog(id){const dialog=byId(id);if(!dialog.open)dialog.showModal();}
  function showClaude(highlight=false){showDialog('claudeDrawer');if(highlight){byId('check').classList.add('wb-highlight-check');byId('check').focus();setTimeout(()=>byId('check').classList.remove('wb-highlight-check'),2500);}}
  function hideMenu(){byId('itemMenu').hidePopover();}
  document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>byId(button.dataset.close).close());
  for(const id of ['claudeDrawer','subscriptionDrawer','itemEditor','subscriptionEditor','missingTarget']){
    const dialog=byId(id);dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});
  }
  byId('openClaude').onclick=()=>showClaude();byId('networkSummary').onclick=()=>showClaude();
  byId('expandClaude').onclick=()=>{const expanded=byId('claudeDrawer').classList.toggle('full-width');byId('expandClaude').textContent=expanded?'还原抽屉':'展开为全宽';byId('expandClaude').setAttribute('aria-pressed',String(expanded));};
  byId('subscriptionSummary').onclick=byId('manageSubscriptions').onclick=()=>showDialog('subscriptionDrawer');
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!document.querySelector('dialog[open]')){event.preventDefault();byId('launcherSearch').focus();}});

  const typeNames={web:'网页',exe:'客户端',uwp:'客户端',folder:'目录',claude_protected:'检测启动'};
  function symbol(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('icon');svg.setAttribute('aria-hidden','true');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;}
  function appIcon(item){
    const icon=el('span','wb-app-icon '+item.group+(item.launch_type==='claude_protected'||item.id==='claude-web'?' claude':''));icon.setAttribute('aria-hidden','true');
    const glyph={gemini:'✦','ai-studio':'✧','google-flow':'F','antigravity-desktop':'A','antigravity-web':'A','chatgpt-web':'◎','chatgpt-desktop':'◎','claude-desktop':'✳','claude-web':'✳',steam:'S',yy:'YY','clash-desktop':'◈','clash-github':'⌘'};
    if(glyph[item.id])icon.textContent=glyph[item.id];else icon.append(symbol(item.group==='mail'?'mail':item.launch_type==='folder'?'folder':item.group==='network'?'shield':item.launch_type==='web'?'external':'app'));
    if(/^static\/icons\/[a-f0-9]{64}\.png$/.test(item.icon)){
      const fallback=[...icon.childNodes];const img=document.createElement('img');img.alt='';img.loading='lazy';img.src='./api/icons/'+item.icon.split('/').pop();img.onerror=()=>icon.replaceChildren(...fallback);icon.replaceChildren(img);
    }
    return icon;
  }
  function tile(item,zone){
    const card=el('div','wb-tile');card.dataset.id=item.id;card.draggable=zone!=='recent';
    const button=el('button','wb-tile-launch');button.type='button';button.setAttribute('aria-label','打开 '+item.name);button.title=item.name;button.disabled=state.opening.has(item.id);button.append(appIcon(item),el('span','wb-tile-name',item.name));button.onclick=()=>openItem(item);
    const more=el('button','wb-tile-more','⋯');more.type='button';more.setAttribute('aria-label',item.name+' 更多操作');more.onclick=event=>openMenu(item,event);
    card.append(button,el('span','wb-tile-kind',typeNames[item.launch_type]),more);card.oncontextmenu=event=>{event.preventDefault();openMenu(item,event);};
    card.ondragstart=event=>{state.drag={id:item.id,zone};event.dataTransfer.setData('text/plain',item.id);event.dataTransfer.effectAllowed='move';card.classList.add('dragging');};
    card.ondragend=()=>{state.drag=null;document.querySelectorAll('.dragging,.drag-over').forEach(x=>x.classList.remove('dragging','drag-over'));};
    card.ondragover=event=>{const source=state.items.find(x=>x.id===state.drag?.id);if(source&&(source.group===item.group||zone==='pinned'&&state.drag.zone==='pinned')){event.preventDefault();card.classList.add('drag-over');event.dataTransfer.dropEffect='move';}};
    card.ondragleave=()=>card.classList.remove('drag-over');
    card.ondrop=async event=>{event.preventDefault();card.classList.remove('drag-over');if(!state.drag||state.drag.id===item.id||state.mutation)return;const source=state.items.find(x=>x.id===state.drag.id);if(!source||source.group!==item.group&&!(zone==='pinned'&&state.drag.zone==='pinned'))return;const ids=state.items.map(x=>x.id).filter(x=>x!==source.id);ids.splice(ids.indexOf(item.id),0,source.id);state.mutation=true;try{await request('launcher/items/reorder',{ids});await loadItems();toast('顺序已保存');}catch(error){toast(error.message);}finally{state.mutation=false;}};
    return card;
  }
  function fillRow(id,items,empty,zone){const host=byId(id);host.replaceChildren();items.forEach(item=>host.append(tile(item,zone)));if(!items.length)host.append(el('p','wb-empty',empty));}
  function renderItems(){
    const visible=state.items.filter(matches);const filtered=visible.filter(item=>state.group==='all'||item.group===state.group||state.group==='pinned'&&item.pinned);
    byId('itemCount').textContent=byId('launcherSearch').value?filtered.length+' 个匹配项目':state.items.length+' 个快捷入口';
    fillRow('pinnedItems',visible.filter(x=>x.pinned),'在项目菜单中选择“置顶”，常用工具就会出现在这里。','pinned');
    fillRow('recentItems',visible.filter(x=>x.last_used>0).sort((a,b)=>b.last_used-a.last_used).slice(0,5),'打开过的项目会出现在这里。','recent');
    const filters=byId('groupFilters');filters.replaceChildren();
    for(const group of [{id:'all',name:'全部项目'},...state.groups]){const b=el('button','wb-filter'+(state.group===group.id?' active':''),group.name);b.setAttribute('aria-pressed',String(state.group===group.id));b.onclick=()=>{state.group=group.id;renderItems();};filters.append(b);}
    const host=byId('launcherGroups');host.replaceChildren();
    const groups=state.group==='pinned'?[{id:'pinned',name:'常用置顶'}]:state.groups.filter(g=>g.id!=='pinned'&&(state.group==='all'||g.id===state.group));
    let shown=0;
    for(const group of groups){const items=filtered.filter(item=>group.id==='pinned'?item.pinned:item.group===group.id);if(!items.length&&byId('launcherSearch').value)continue;const section=el('section','wb-group');const heading=el('div','wb-group-heading');heading.append(el('h2','',group.name),el('small','',String(items.length).padStart(2,'0')));section.append(heading);if(items.length){const grid=el('div','wb-group-grid');items.forEach(item=>grid.append(tile(item,'group')));section.append(grid);shown+=items.length;}else{section.append(el('p','wb-empty','还没有项目，点击“添加项目”放入常用目录和工具。'));}host.append(section);}
    // Entries explicitly assigned to the pinned group still appear when viewing all categories.
    if(state.group==='all'){const items=filtered.filter(item=>item.group==='pinned');if(items.length){const section=el('section','wb-group');section.append(el('h2','wb-group-heading','常用置顶分组'));const grid=el('div','wb-group-grid');items.forEach(item=>grid.append(tile(item,'group')));section.append(grid);host.prepend(section);shown+=items.length;}}
    byId('searchEmpty').hidden=!(byId('launcherSearch').value&&!shown);
  }
  async function loadItems(){const data=await request('launcher/items');state.items=data.items;state.groups=data.groups;byId('launcherError').hidden=true;renderItems();}
  async function reloadItems(){try{await loadItems();}catch(error){byId('launcherError').hidden=false;byId('launcherError').querySelector('span').textContent=error.message;byId('itemCount').textContent='入口暂不可用';}}
  byId('launcherSearch').oninput=renderItems;byId('retryLauncher').onclick=reloadItems;
  async function openItem(item){
    if(state.opening.has(item.id))return;state.opening.add(item.id);renderItems();
    try{const result=await request('launcher/open',{id:item.id});if(result.action==='open_claude_drawer'){showClaude(true);return;}toast(result.warning||({already_running:'客户端已运行',activated:'已切换到客户端',opened:'已发送打开请求'}[result.status]||'已打开'));await loadItems();}
    catch(error){if(['target_missing','working_dir_missing','browser_missing'].includes(error.data?.code)){state.missing=item;byId('missingMessage').textContent=error.message;byId('missingLocate').hidden=!['exe','folder'].includes(item.launch_type);byId('missingLocate').textContent=item.launch_type==='folder'?'选择本地文件夹':'手动选择本地 EXE';byId('missingOfficial').hidden=!item.target_config.official_url;byId('missingError').textContent='';showDialog('missingTarget');}else toast(error.message);}
    finally{state.opening.delete(item.id);renderItems();}
  }
  function openMenu(item,event){
    const menu=byId('itemMenu');hideMenu();menu.replaceChildren();
    const add=(label,action)=>{const button=el('button','',label);button.setAttribute('role','menuitem');button.onclick=async()=>{hideMenu();try{await action();}catch(error){toast(error.message);}};menu.append(button);};
    add('编辑配置',()=>editItem(item));
    add(item.pinned?'取消置顶':'置顶',async()=>{await request('launcher/items/update',{id:item.id,pinned:!item.pinned});await loadItems();});
    if(['exe','folder'].includes(item.launch_type))add('打开所在位置',()=>request('launcher/reveal',{id:item.id}));
    add(item.launch_type==='web'?'打开官网 / 网页':'打开官网 / 下载页',()=>request('launcher/official',{id:item.id}));
    add('从启动台移除',async()=>{if(!confirm('从启动台移除“'+item.name+'”？本机软件和文件会保留。'))return;await request('launcher/items/delete',{id:item.id});await loadItems();toast('已移除快捷入口');});
    menu.showPopover();const bounds=event.currentTarget.getBoundingClientRect();const x=event.type==='contextmenu'?event.clientX:bounds.right-180;const y=event.type==='contextmenu'?event.clientY:bounds.bottom;
    menu.style.left=Math.max(8,Math.min(x,innerWidth-menu.offsetWidth-8))+'px';menu.style.top=Math.max(8,Math.min(y,innerHeight-menu.offsetHeight-8))+'px';menu.querySelector('button').focus();
  }

  function itemTypeChanged(){const kind=byId('itemType').value;byId('targetField').hidden=kind==='claude_protected';byId('protectedNote').hidden=kind!=='claude_protected';byId('browserFields').hidden=kind!=='web';byId('advancedItemFields').hidden=!['exe','uwp'].includes(kind);byId('itemArgs').closest('label').hidden=kind!=='exe';byId('itemWorkingDir').closest('label').hidden=kind!=='exe';byId('pickItemTarget').hidden=!['exe','folder'].includes(kind);byId('targetLabel').textContent={web:'网址',exe:'本机程序位置',folder:'文件夹位置',uwp:'应用标识 AUMID'}[kind]||'目标';byId('itemTarget').placeholder=kind==='web'?'https://':kind==='uwp'?'PackageFamilyName!App':'点击右侧选择本机位置';byId('itemTarget').type=kind==='web'?'url':'text';byId('itemProfile').disabled=byId('itemBrowser').value==='default';}
  function editItem(item=null){
    state.editing=item;byId('itemForm').reset();byId('itemFormError').textContent='';byId('itemEditorTitle').textContent=item?'编辑项目':'添加项目';
    byId('itemGroup').replaceChildren(...state.groups.map(group=>{const option=el('option','',group.name);option.value=group.id;return option;}));
    byId('itemGroup').value=item?.group||(state.group!=='all'?state.group:'projects');byId('itemType').value=item?.launch_type||'web';byId('itemType').disabled=item?.launch_type==='claude_protected';
    byId('itemName').value=item?.name||'';byId('itemPinned').checked=item?.pinned||false;
    const c=item?.target_config||{};for(const [id,key] of [['itemTarget','target'],['itemArgs','args'],['itemWorkingDir','working_dir'],['itemProfile','profile'],['itemSingleton','singleton_process'],['itemOfficial','official_url']])byId(id).value=c[key]||'';
    byId('itemBrowser').value=c.browser||'default';byId('advancedItemFields').open=false;itemTypeChanged();window.WorkbenchExtras?.editorOpened(Boolean(item));showDialog('itemEditor');
  }
  byId('addItem').onclick=()=>{if(!state.groups.length){toast('请先恢复启动台数据连接');return;}editItem();};
  byId('itemType').onchange=byId('itemBrowser').onchange=itemTypeChanged;
  byId('pickItemTarget').onclick=async()=>{const b=byId('pickItemTarget');b.disabled=true;byId('itemFormError').textContent='';try{const result=await request('launcher/pick-target',{launch_type:byId('itemType').value});if(!result.cancelled)byId('itemTarget').value=result.target;}catch(error){byId('itemFormError').textContent=error.message;}finally{b.disabled=false;}};
  byId('itemForm').onsubmit=async event=>{
    event.preventDefault();const kind=byId('itemType').value;const c={target:kind==='claude_protected'?'':byId('itemTarget').value.trim(),args:kind==='exe'?byId('itemArgs').value:'',working_dir:kind==='exe'?byId('itemWorkingDir').value.trim():'',browser:kind==='web'?byId('itemBrowser').value:'default',profile:kind==='web'&&byId('itemBrowser').value!=='default'?byId('itemProfile').value.trim():'',singleton_process:['exe','uwp'].includes(kind)?byId('itemSingleton').value.trim():'',official_url:byId('itemOfficial').value.trim()};
    const payload={id:state.editing?.id||'custom-'+crypto.randomUUID(),name:byId('itemName').value.trim(),group:byId('itemGroup').value,launch_type:kind,pinned:byId('itemPinned').checked,icon:state.editing?.icon||'builtin:'+byId('itemGroup').value,target_config:c};
    byId('saveItem').disabled=true;byId('itemFormError').textContent='';try{await request('launcher/items/'+(state.editing?'update':'create'),payload);byId('itemEditor').close();await loadItems();toast('项目已保存');}catch(error){byId('itemFormError').textContent=error.message;}finally{byId('saveItem').disabled=false;}
  };
  byId('missingEdit').onclick=()=>{byId('missingTarget').close();editItem(state.missing);};
  byId('missingLocate').onclick=async()=>{const b=byId('missingLocate');b.disabled=true;try{const result=await request('launcher/pick-target',{launch_type:state.missing.launch_type});if(!result.cancelled){await request('launcher/items/update',{id:state.missing.id,target_config:{target:result.target}});byId('missingTarget').close();await loadItems();toast('位置已保存，点击项目即可打开');}}catch(error){byId('missingError').textContent=error.message;}finally{b.disabled=false;}};
  byId('missingOfficial').onclick=async()=>{try{await request('launcher/official',{id:state.missing.id});toast('已打开官网');}catch(error){byId('missingError').textContent=error.message;}};

  function expiryLabel(sub){if(sub.remaining_days===null)return '待填写';if(sub.remaining_days<0)return '已过期 '+Math.abs(sub.remaining_days)+' 天';if(sub.remaining_days===0)return '今天到期';return '剩余 '+sub.remaining_days+' 天';}
  function expiryBadge(sub,prefix=''){return el('span','wb-expiry '+sub.expiry_status,prefix+expiryLabel(sub));}
  function renderSubscriptions(){
    const summary=byId('subscriptionSummary');summary.replaceChildren();const primary=state.subscriptions.filter(s=>['static-ip','vpn'].includes(s.id));for(const sub of primary)summary.append(expiryBadge(sub,(sub.id==='static-ip'?'静态 IP':'VPN')+'：'));if(!primary.length)summary.textContent='订阅与到期 · 点击管理';
    const host=byId('subscriptionCards');host.replaceChildren();
    for(const sub of state.subscriptions){const card=el('article','wb-sub-card');const header=el('header');const title=el('div');title.append(el('h3','',sub.name),el('p','wb-sub-purpose',sub.purpose||'用途待填写'));const edit=el('button','','编辑');edit.onclick=()=>editSubscription(sub);header.append(title,edit);card.append(header,expiryBadge(sub),el('p','wb-sub-date',sub.expire_date?'到期日 '+sub.expire_date:'尚未设置到期日期'));
      const links=el('div','wb-sub-links');for(const [field,label] of [['renew_url','购买 / 续费 ↗'],['admin_url','账户后台 ↗'],['official_url','官网 ↗']]){const b=el('button','',label);b.disabled=!sub[field];b.title=sub[field]?'打开已保存的入口':'编辑订阅后填写入口';b.onclick=async()=>{try{await request('subscriptions/open',{id:sub.id,link:field});}catch(error){toast(error.message);}};links.append(b);}card.append(links);
      if(sub.ip_info)card.append(el('p','wb-sub-notes',sub.ip_info));if(sub.notes)card.append(el('p','wb-sub-notes',sub.notes));
      const copies=el('div','wb-sub-copies');for(const [field,label] of [['ip_info','复制 IP / 端口'],['notes','复制备注']]){const b=el('button','',label);b.disabled=!sub[field];b.onclick=async()=>{try{await navigator.clipboard.writeText(sub[field]);toast('已复制');}catch{toast('复制未完成，请选中文字手动复制');}};copies.append(b);}card.append(copies);
      const meta=el('div','wb-sub-meta');meta.append(el('span','',sub.update_source));const remove=el('button','','移除订阅');remove.onclick=async()=>{if(!confirm('移除这条订阅记录？不会取消服务商套餐。'))return;try{await request('subscriptions/delete',{id:sub.id});await loadSubscriptions();}catch(error){toast(error.message);}};meta.append(remove);card.append(meta);host.append(card);
    }
    if(!state.subscriptions.length)host.append(el('p','wb-empty','还没有订阅记录。点击下方按钮添加。'));
  }
  async function loadSubscriptions(){const data=await request('subscriptions');state.subscriptions=data.subscriptions;renderSubscriptions();}
  function editSubscription(sub=null){state.editingSub=sub;byId('subscriptionForm').reset();byId('subFormError').textContent='';byId('subEditorTitle').textContent=sub?'编辑订阅':'添加订阅';for(const [id,key] of [['subName','name'],['subExpiry','expire_date'],['subPurpose','purpose'],['subOfficial','official_url'],['subRenew','renew_url'],['subAdmin','admin_url'],['subIp','ip_info'],['subNotes','notes']])byId(id).value=sub?.[key]||'';showDialog('subscriptionEditor');}
  byId('addSubscription').onclick=()=>editSubscription();
  byId('subscriptionForm').onsubmit=async event=>{event.preventDefault();const payload={id:state.editingSub?.id||'sub-'+crypto.randomUUID(),update_source:'手动填写'};for(const [id,key] of [['subName','name'],['subExpiry','expire_date'],['subPurpose','purpose'],['subOfficial','official_url'],['subRenew','renew_url'],['subAdmin','admin_url'],['subIp','ip_info'],['subNotes','notes']])payload[key]=byId(id).value.trim();byId('saveSubscription').disabled=true;try{await request('subscriptions/'+(state.editingSub?'update':'create'),payload);byId('subscriptionEditor').close();await loadSubscriptions();toast('订阅已保存');}catch(error){byId('subFormError').textContent=error.message;}finally{byId('saveSubscription').disabled=false;}};
  // Recalculate date-only countdowns across midnight without querying any external provider.
  let subscriptionDay=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  setInterval(()=>{const day=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});if(day!==subscriptionDay){subscriptionDay=day;loadSubscriptions().catch(()=>{});}},60000);

  function indicator(id,text,status){const target=byId(id);target.textContent=text;target.className='wb-indicator '+status;}
  function renderExit(data){let label='待查询';if(data.checked)label=data.region?(new Intl.DisplayNames(['zh-CN'],{type:'region'}).of(data.region)||data.region):'待确认';indicator('exitIndicator','出口：'+label,'');byId('exitIndicator').title=data.detail+(data.checked?' · '+new Date(data.checked*1000).toLocaleString():'');}
  async function localStatus(){try{const data=await request('status');indicator('clashIndicator','Clash：'+({running:'运行中',stopped:'未运行',unknown:'待确认'}[data.clash]||'待确认'),data.clash==='running'?'good':data.clash==='stopped'?'bad':'');indicator('proxyIndicator','代理：'+(data.proxy==='connected'?'连通':'断开'),data.proxy==='connected'?'good':'bad');renderExit(data.exit);}catch{indicator('clashIndicator','Clash：待确认','');indicator('proxyIndicator','代理：待确认','');}}
  async function refreshNetwork(){if(state.refreshing)return;state.refreshing=true;byId('refreshNetwork').disabled=true;try{await localStatus();renderExit(await request('status/refresh-exit',{}));}catch(error){toast(error.message);}finally{state.refreshing=false;byId('refreshNetwork').disabled=false;}}
  byId('refreshNetwork').onclick=refreshNetwork;
  window.addEventListener('claude-state',event=>{const s=event.detail;const [label,status]=s.offline?['服务离线','warn']:s.sampling?['采样中','warn']:s.busy?['检测中','warn']:s.changed?['需重检','warn']:s.failed?['需处理','bad']:s.expired?['已过期','warn']:s.ready?['可手动启动','good']:s.report||s.error?['待核验','warn']:['待检测',''];indicator('claudeIndicator','Claude：'+label,status);});
  window.Workbench={request,state,toast,showDialog,showClaude,loadItems,loadSubscriptions,editItem,appIcon,searchKeys};
  reloadItems();loadSubscriptions().catch(error=>{byId('subscriptionSummary').textContent='订阅：暂不可用';byId('subscriptionCards').replaceChildren(el('p','wb-empty',error.message));});refreshNetwork();
  setInterval(localStatus,10000);
})();
