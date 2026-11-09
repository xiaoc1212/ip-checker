/* The workbench manages saved IDs; only the existing Claude UI can request a protected launch. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  byId('settingsDialog').append(byId('settingsContent').content.cloneNode(true));
  byId('settingsContent').remove();
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
  function switchView(id){
    document.querySelectorAll('.wb-view').forEach(view=>view.hidden=view.id!==id);
    document.querySelectorAll('[data-view]').forEach(tab=>{tab.setAttribute('aria-selected',String(tab.dataset.view===id));tab.tabIndex=tab.dataset.view===id?0:-1;});
    hideMenu();
    if(id==='subscriptionDrawer')loadProviderUsage().catch(()=>{});
  }
  document.querySelectorAll('[data-view]').forEach(tab=>{tab.onclick=()=>switchView(tab.dataset.view);tab.onkeydown=event=>{const tabs=[...document.querySelectorAll('[data-view]')];let index=tabs.indexOf(tab);if(event.key==='ArrowRight')index=(index+1)%tabs.length;else if(event.key==='ArrowLeft')index=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')index=0;else if(event.key==='End')index=tabs.length-1;else return;event.preventDefault();tabs[index].click();tabs[index].focus();};});
  function showDialog(id){if(['claudeDrawer','subscriptionDrawer','settingsDialog'].includes(id)){switchView(id==='settingsDialog'?'subscriptionDrawer':id);return;}const dialog=byId(id);if(!dialog.open)dialog.showModal();}
  function showClaude(highlight=false){showDialog('claudeDrawer');if(highlight){byId('check').classList.add('wb-highlight-check');byId('check').focus();setTimeout(()=>byId('check').classList.remove('wb-highlight-check'),2500);}}
  document.querySelector('.wb-brand').onclick=event=>{event.preventDefault();switchView('launcherView');};
  function hideMenu(){byId('itemMenu').hidePopover();}
  document.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>byId(button.dataset.close).close());
  for(const id of ['itemEditor','subscriptionEditor','missingTarget']){
    const dialog=byId(id);dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});
  }
  byId('openClaude').onclick=()=>showClaude();byId('claudeIndicator').onclick=()=>showClaude();
  byId('clashIndicator').onclick=byId('exitIndicator').onclick=()=>switchView('subscriptionDrawer');
  byId('subscriptionSummary').onclick=byId('manageSubscriptions').onclick=()=>showDialog('subscriptionDrawer');
  document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!document.querySelector('dialog[open]')){event.preventDefault();switchView('launcherView');byId('launcherSearch').focus();}});

  const typeNames={web:'网页',exe:'客户端',uwp:'客户端',folder:'目录',claude_protected:'检测启动'};
  function symbol(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('icon');svg.setAttribute('aria-hidden','true');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href','#i-'+name);svg.append(use);return svg;}
  // Static, local SVG artwork. Never interpolate configuration values into markup.
  let iconSequence=0;
  function brandIcon(id){
    const swirl=Array.from({length:6},(_,i)=>'<path transform="rotate('+i*60+' 20 20)" d="M20 11c-9-6-17 6-10 12l10 6 7-4V15l-7-4-7 4v10"/>').join('');
    const art={
      gemini:'<defs><linearGradient id="gemini-gradient" x2="1" y2="1"><stop stop-color="#64B5FF"/><stop offset=".6" stop-color="#7C6CFA"/><stop offset="1" stop-color="#C084FC"/></linearGradient></defs><path fill="url(#gemini-gradient)" d="M20 2c2 12 6 16 18 18-12 2-16 6-18 18C18 26 14 22 2 20 14 18 18 14 20 2Z"/>',
      'ai-studio':'<rect x="3" y="5" width="34" height="30" rx="6" fill="#103B69" stroke="#38BDF8" stroke-width="2"/><path d="M4 13h32M9 9h2m3 0h2m-3 9-5 5 5 5m14-10 5 5-5 5m-5-11-4 13" stroke="#55CDFF" stroke-width="2.5" fill="none"/>',
      'google-flow':'<path d="M6 24C-1 8 14 0 26 9c7 5 13 2 12-2 6 17-7 21-17 14C10 14 4 18 6 24Z" fill="#2DD4BF"/><path d="M34 16c7 16-8 24-20 15C7 26 1 29 2 33c-6-17 7-21 17-14 11 7 17 3 15-3Z" fill="#0D9488"/>',
      'antigravity-desktop':'<path fill="#A78BFA" d="m20 2 18 32H2Zm0 12L9 30h22Z" fill-rule="evenodd"/><path d="m20 8 7 13H13Z" fill="#FB923C"/><path d="M10 35h20" stroke="#FB923C" stroke-width="3"/>',
      'chatgpt-web':'<g fill="none" stroke="#10B981" stroke-width="1.8" stroke-linejoin="round">'+swirl+'</g>',
      'claude-web':'<g stroke="#E7A276" stroke-width="2.4" stroke-linecap="square">'+Array.from({length:12},(_,i)=>'<path transform="rotate('+i*30+' 20 20)" d="M20 4v10"/>').join('')+'</g><circle cx="20" cy="20" r="5" fill="#D98B5F"/>',
      'qq-mail':'<rect x="3" y="10" width="34" height="25" rx="5" fill="#FBBF24"/><path d="m4 12 16 13 16-13" stroke="#142D50" stroke-width="3" fill="none"/><ellipse cx="20" cy="12" rx="7" ry="9" fill="#183453"/><ellipse cx="20" cy="14" rx="5" ry="5" fill="#F3F4F6"/><path d="m17 11 3-2 3 2-3 2Z" fill="#FB923C"/>',
      gmail:'<path d="M4 10v23h7V16l9 7 9-7v17h7V10" fill="#F3F4F6"/><path d="M4 33V10l7 5v18" fill="#4285F4"/><path d="M29 15v18h7V10" fill="#34A853"/><path d="m4 8 16 12L36 8v8L20 28 4 16Z" fill="#EA4335"/><path d="m29 13 7-5v8l-7 5" fill="#FBBC04"/>',
      'clash-desktop':'<path d="M5 17 4 5l11 7q5-2 10 0L36 5l-1 12c7 19-37 24-30 0Z" fill="#60A5FA"/><path d="m8 11 6 4-6 2m24-6-6 4 6 2" fill="#1E3A5F"/><path d="m12 22 4 1m8 0 4-1m-11 6 3 2 3-2" stroke="#102342" stroke-width="2.5" stroke-linecap="round" fill="none"/>',
      'clash-github':'<path fill="#F3F4F6" d="M20 3a17 17 0 0 0-5.4 33.1c.9.2 1.2-.4 1.2-.8v-3.2c-5 .9-6-2.1-6-2.1-.8-2-2-2.5-2-2.5-1.7-1.2.1-1.2.1-1.2 1.8.1 2.7 1.8 2.7 1.8 1.6 2.7 4.2 1.9 5.2 1.4.2-1.2.6-1.9 1.1-2.4-4-.4-8.2-2-8.2-9 0-2 .7-3.6 1.8-4.8-.2-.5-.8-2.4.2-4.8 0 0 1.5-.5 5 1.8a17 17 0 0 1 9 0c3.5-2.3 5-1.8 5-1.8 1 2.4.4 4.3.2 4.8 1.1 1.2 1.8 2.8 1.8 4.8 0 7-4.2 8.6-8.2 9 .7.6 1.3 1.7 1.3 3.4v4.8c0 .4.3 1 1.2.8A17 17 0 0 0 20 3Z"/>',
      'static-ip-admin':'<g fill="#18314D" stroke="#7DD3FC" stroke-width="1.7"><rect x="5" y="4" width="30" height="9" rx="2"/><rect x="5" y="16" width="30" height="9" rx="2"/><rect x="5" y="28" width="30" height="9" rx="2"/></g><path d="M10 8h3m-3 12h3m-3 12h3" stroke="#10B981" stroke-width="3"/><path d="M21 8h9m-9 12h9m-9 12h9" stroke="#7DD3FC" stroke-width="2"/>',
      'vpn-web':'<path d="m20 3 15 5v12c0 9-15 17-15 17S5 29 5 20V8Z" fill="#123A34" stroke="#2DD4BF" stroke-width="2"/><rect x="13" y="18" width="14" height="11" rx="2" fill="#2DD4BF"/><path d="M16 18v-4a4 4 0 0 1 8 0v4" fill="none" stroke="#B7F7E5" stroke-width="2"/><circle cx="20" cy="23" r="1.5" fill="#123A34"/>',
      steam:'<circle cx="20" cy="20" r="18" fill="#193A59"/><path d="m3 25 10 4 12-11" stroke="#D7E9F6" stroke-width="5"/><circle cx="27" cy="14" r="8" fill="#193A59" stroke="#D7E9F6" stroke-width="3"/><circle cx="13" cy="29" r="5" fill="#193A59" stroke="#D7E9F6" stroke-width="2"/>',
      yy:'<path d="M3 14 9 3l11 6 11-6 6 11-4 20-13 4-13-4Z" fill="#FACC15"/><path d="m10 15 7 8v8m13-16-7 8v8" stroke="#503A11" stroke-width="4" stroke-linecap="round" fill="none"/>'
    };
    if(id==='antigravity-web')id='antigravity-desktop';if(id==='chatgpt-desktop')id='chatgpt-web';if(id==='claude-desktop')id='claude-web';
    if(!art[id])return null;const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 40 40');svg.setAttribute('aria-hidden','true');svg.innerHTML=art[id];const gradient=svg.querySelector('linearGradient');if(gradient){const unique='brand-gradient-'+(++iconSequence);gradient.id=unique;svg.querySelector('path[fill^="url"]').setAttribute('fill','url(#'+unique+')');}return svg;
  }
  function appIcon(item){
    const icon=el('span','wb-app-icon '+item.group+(item.launch_type==='claude_protected'||item.id==='claude-web'?' claude':''));icon.setAttribute('aria-hidden','true');
    const brand=brandIcon(item.id);icon.append(brand||symbol(item.group==='mail'?'mail':item.launch_type==='folder'?'folder':item.group==='network'?'shield':item.launch_type==='web'?'external':'app'));
    // First try the official website favicon for known public services, then Google's favicon cache.
    // Custom/internal URLs never get sent to a third-party icon service.
    const logoIds=new Set(['runninghub','xiaoyunque','libtv','jimeng','aigei','unity-store','youtube',
      'bilibili','xiaozhi','priceai','opentherank','wgetcloud','justmysocks','flyingbird',
      'tag-internet','nexitally','mojie','astrill','letsvpn']);
    if(!brand&&item.launch_type==='web'&&logoIds.has(item.id)){
      try{
        const site=new URL(item.target_config.target);
        if(site.protocol==='https:'){
          const favicon=document.createElement('img');favicon.className='wb-web-favicon';
          favicon.alt='';favicon.loading='lazy';favicon.referrerPolicy='no-referrer';
          const fallback=[...icon.childNodes];let retried=false;
          favicon.onerror=()=>{
            if(!retried){retried=true;favicon.src='https://www.google.com/s2/favicons?domain='+encodeURIComponent(site.hostname)+'&sz=64';}
            else icon.replaceChildren(...fallback);
          };
          icon.replaceChildren(favicon);favicon.src=site.origin+'/favicon.ico';
        }
      }catch{}
    }
    if(!brand&&/^static\/icons\/[a-f0-9]{64}\.png$/.test(item.icon)){
      const fallback=[...icon.childNodes];const img=document.createElement('img');img.alt='';img.loading='lazy';img.src='./api/icons/'+item.icon.split('/').pop();img.onerror=()=>icon.replaceChildren(...fallback);icon.replaceChildren(img);
    }
    return icon;
  }
  function tile(item,zone){
    const card=el('div','wb-tile');card.dataset.id=item.id;card.draggable=zone!=='recent';
    const button=el('button','wb-tile-launch');button.type='button';button.setAttribute('aria-label','打开 '+item.name);button.title=item.name;button.disabled=state.opening.has(item.id);const label=el('span','wb-tile-label');label.append(el('span','wb-tile-name',item.name));if(item.target_config?.profile)label.append(el('span','wb-tile-profile',item.target_config.profile));button.append(appIcon(item),label);button.onclick=()=>openItem(item);
    const more=el('button','wb-tile-more','⋯');more.type='button';more.setAttribute('aria-label',item.name+' 更多操作');more.onclick=event=>openMenu(item,event);
    card.append(button,el('span','wb-tile-kind '+item.launch_type,typeNames[item.launch_type]),more);card.oncontextmenu=event=>{event.preventDefault();openMenu(item,event);};
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
    fillRow('pinnedItems',visible.filter(x=>x.pinned),'在项目菜单中选择“置顶”，常用工具就会出现在这里。','pinned');
    fillRow('recentItems',visible.filter(x=>x.last_used>0).sort((a,b)=>b.last_used-a.last_used).slice(0,5),'打开过的项目会出现在这里。','recent');
    const host=byId('launcherGroups');host.replaceChildren();
    const groups=state.group==='pinned'?[{id:'pinned',name:'常用置顶'}]:state.groups.filter(g=>g.id!=='pinned'&&(state.group==='all'||g.id===state.group));
    let shown=0;
    for(const group of groups){const items=filtered.filter(item=>group.id==='pinned'?item.pinned:item.group===group.id);if(!items.length&&byId('launcherSearch').value)continue;const section=el('section','wb-group');const heading=el('div','wb-group-heading');const glyphs={google_ai:'✦',gpt_claude:'✳',daily_apps:'▣',mail:'✉',network:'◎',projects:'⌘',ai_creative:'🎬',assets:'◈',media:'▶',ai_reference:'▥',vpn_services:'🌐',pinned:'★'};
      const marker=el('span','wb-group-glyph',glyphs[group.id]||'◦');marker.setAttribute('aria-hidden','true');
      heading.append(marker,el('h2','',group.name),el('small','',String(items.length).padStart(2,'0')));section.append(heading);if(items.length){const grid=el('div','wb-group-grid');items.forEach(item=>grid.append(tile(item,'group')));section.append(grid);shown+=items.length;}else{section.append(el('p','wb-empty','还没有项目，点击“添加项目”放入常用目录和工具。'));}host.append(section);}
    // Entries explicitly assigned to the pinned group still appear when viewing all categories.
    if(state.group==='all'){const items=filtered.filter(item=>item.group==='pinned');if(items.length){const section=el('section','wb-group');section.append(el('h2','wb-group-heading','常用置顶分组'));const grid=el('div','wb-group-grid');items.forEach(item=>grid.append(tile(item,'group')));section.append(grid);host.prepend(section);shown+=items.length;}}
    byId('searchEmpty').hidden=!(byId('launcherSearch').value&&!shown);
  }
  async function loadItems(){const data=await request('launcher/items');state.items=data.items;state.groups=data.groups;byId('launcherError').hidden=true;renderItems();}
  async function reloadItems(){try{await loadItems();}catch(error){byId('launcherError').hidden=false;byId('launcherError').querySelector('span').textContent=error.message;}}
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
  let providerSnapshot=null;
  const ROLES={primary:'主用 · 手动标记',backup:'备用',inactive:'停用 / 未使用',unassigned:'未分类'};
  function providerQuota(sub){
    const record=providerSnapshot?.providers?.[sub.provider_name];
    const synced=Boolean(record?.available);
    const quota=n=>Number.isFinite(n)&&n>=0?n/1073741824:null;
    const used=synced?quota(record.used_bytes):null,total=synced?quota(record.total_bytes):null;
    const manualUsed=sub.traffic_used_gb===''?null:Number(sub.traffic_used_gb);
    const manualTotal=sub.traffic_total_gb===''?null:Number(sub.traffic_total_gb);
    return {used:used??manualUsed,total:total??manualTotal,expiry:record?.expire_date||sub.expire_date,
      source:synced?'Clash 订阅信息（本机读取）':'手动填写',synced};
  }
  function expiryOf(date,defaultDays){
    if(!date)return {days:defaultDays,label:'到期日待填写'};
    const today=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
    const days=Math.round((Date.parse(date+'T00:00:00Z')-Date.parse(today+'T00:00:00Z'))/86400000);
    if(!Number.isFinite(days))return {days:null,label:'到期日待确认'};
    return {days,label:days<0?'已过期 '+Math.abs(days)+' 天':days===0?'今天到期':'剩余 '+days+' 天'};
  }
  function renderSubscriptions(){
    const summary=byId('subscriptionSummary');const primary=state.subscriptions.find(s=>s.id==='static-ip');
    summary.replaceChildren(el('span','wb-expiry '+(primary?.expiry_status||'unfilled'),'⏳ IP：'+(!primary||primary.remaining_days===null?'待配置':expiryLabel(primary))));
    summary.title=state.subscriptions.map(s=>s.name+'：'+expiryLabel(s)).join('\n');
    const host=byId('subscriptionCards');host.replaceChildren();
    const roles=['primary','backup','inactive','unassigned'];
    const sorted=[...state.subscriptions].sort((a,b)=>roles.indexOf(a.usage_role)-roles.indexOf(b.usage_role));
    let lastRole='';
    for(const sub of sorted){
      const role=sub.usage_role||'unassigned';
      if(role!==lastRole){
        lastRole=role;
        const heading=el('div','wb-sub-title-row');heading.append(el('h3','',({primary:'● 主用订阅',backup:'◌ 备用订阅',inactive:'○ 停用 / 未使用',unassigned:'▫ 未分类订阅'})[role]));
        heading.style.gridColumn='1 / -1';host.append(heading);
      }
      const quota=providerQuota(sub),expiry=expiryOf(quota.expiry,sub.remaining_days);
      const card=el('article','wb-sub-card');card.dataset.subscriptionId=sub.id;
      const header=el('header'),title=el('div');title.append(el('h3','',sub.name));
      const tags=el('div','wb-sub-badges');tags.append(el('span','wb-sub-tag '+role,ROLES[role]));
      tags.append(el('span','wb-sub-tag',quota.source));
      title.append(tags);
      const edit=el('button','wb-secondary wb-sub-edit','✎ 编辑');edit.type='button';edit.onclick=()=>editSubscription(sub);
      header.append(title,edit);card.append(header);
      const meter=el('div','wb-sub-meter '+(expiry.days===null?'unfilled':expiry.days<0?'expired':expiry.days<=3?'urgent':'normal'));
      meter.append(el('strong','wb-sub-status',expiry.label));card.append(meter);
      if(quota.expiry)card.append(el('p','wb-sub-date','到期 '+quota.expiry));
      const traffic=el('div','wb-sub-traffic');
      const fmt=n=>n===null?'—':n.toFixed(n<10?2:1)+' GB';
      traffic.append(el('span','','已用 '+fmt(quota.used)),el('span','','剩余 '+(quota.total!==null&&quota.used!==null?fmt(Math.max(0,quota.total-quota.used)):'—')));
      card.append(traffic);
      if(quota.total!==null){
        card.append(el('p','wb-sub-footer','套餐总流量 '+fmt(quota.total)));
        if(quota.total>0&&quota.used!==null){
          const bar=el('div','wb-sub-traffic-meter');const fill=el('span');fill.style.width=Math.max(0,Math.min(100,quota.used/quota.total*100))+'%';bar.append(fill);card.append(bar);
        }
      }else card.append(el('p','wb-sub-footer','套餐总流量未登记'));
      const links=el('div','wb-sub-links');
      for(const [field,label] of [['admin_url','管理后台'],['renew_url','续费'],['official_url','官网'],['ip_info','复制 IP']]){
        if(!sub[field])continue;
        const button=el('button','',label);button.type='button';button.title='打开已保存的链接或复制 IP';
        button.onclick=async()=>{try{if(field==='ip_info'){await navigator.clipboard.writeText(sub.ip_info);toast('已复制 IP');}else await request('subscriptions/open',{id:sub.id,link:field});}catch(error){toast(error.message||'操作未完成');}};
        links.append(button);
      }
      if(links.childElementCount)card.append(links);
      if(sub.ip_info)card.title='登记 IP：'+sub.ip_info+(sub.notes?'\n备注：'+sub.notes:'');
      else if(sub.notes)card.title=sub.notes;
      const meta=el('div','wb-sub-meta');const remove=el('button','','移除');remove.type='button';remove.onclick=async()=>{
        if(!confirm('从工作台移除“'+sub.name+'”？不会取消商家订阅。'))return;
        try{await request('subscriptions/delete',{id:sub.id});await loadSubscriptions();}catch(error){toast(error.message);}
      };
      meta.append(remove);card.append(meta);host.append(card);
    }
    // Show all loaded Clash providers, even if they are inactive or not yet registered.
    // These are read-only ephemeral cards until the user chooses to register one.
    const linked=new Set(state.subscriptions.map(sub=>sub.provider_name).filter(Boolean));
    const unlinked=Object.entries(providerSnapshot?.providers||{}).filter(([name])=>!linked.has(name)&&!['default','global'].includes(name.toLowerCase()));
    if(unlinked.length){
      const heading=el('div','wb-sub-title-row');heading.style.gridColumn='1 / -1';
      heading.append(el('h3','','◈ Clash 中未登记的订阅'));host.append(heading);
      for(const [name,info] of unlinked){
        const card=el('article','wb-sub-card wb-sub-unlinked');
        const title=el('h3','',name),tag=el('span','wb-sub-tag backup','Clash 中已配置 · 使用状态未确认');
        card.append(title,tag);
        const GB=n=>Number.isFinite(n)&&n>=0?(n/1073741824).toFixed(2)+' GB':'—';
        const used=Number.isFinite(info.used_bytes)?info.used_bytes:null,total=Number.isFinite(info.total_bytes)?info.total_bytes:null;
        card.append(el('div','wb-sub-traffic','已用 '+GB(used)+'　/　总额 '+GB(total)));
        card.append(el('div','wb-sub-footer',info.expire_date?'到期 '+info.expire_date:'到期日未提供'));
        const save=el('button','wb-secondary','加入管理');save.onclick=()=>{
          editSubscription();byId('subName').value=name;byId('subProvider').value=name;byId('subRole').value='backup';
        };card.append(save);host.append(card);
      }
    }
    if(!state.subscriptions.length&&!unlinked.length)host.append(el('p','wb-empty','尚未登记订阅，且当前未读取到 Clash 的其他提供者。'));
  }
  async function loadProviderUsage(force=false){
    const btn=byId('refreshProviders');
    if(!force&&providerSnapshot&&Date.now()/1000-providerSnapshot.checked<60)return;
    btn.disabled=true;
    byId('providerNotice').textContent='正在读取 Clash 中保存的套餐信息…';
    try{
      providerSnapshot=await request('subscriptions/providers');
      const names=Object.keys(providerSnapshot.providers||{});
      byId('providerNames').replaceChildren(...names.map(n=>{const option=el('option');option.value=n;return option;}));
      byId('providerNotice').textContent=providerSnapshot.warning||('已读取 '+names.length+' 个 Clash 提供者；按订阅提供者名称精确关联。未导入 Clash 的服务可手动填写。');
      renderSubscriptions();
    }catch(error){providerSnapshot=null;byId('providerNotice').textContent='读取失败：'+error.message;renderSubscriptions();}
    finally{btn.disabled=false;}
  }
  byId('refreshProviders').onclick=async()=>{await refreshNetwork();await loadProviderUsage(true);};
  async function loadSubscriptions(){const data=await request('subscriptions');state.subscriptions=data.subscriptions;renderSubscriptions();}
  function editSubscription(sub=null){
    state.editingSub=sub;byId('subscriptionForm').reset();byId('subFormError').textContent='';
    byId('subEditorTitle').textContent=sub?'编辑订阅':'添加订阅';
    for(const [id,key] of [['subName','name'],['subExpiry','expire_date'],['subPurpose','purpose'],['subOfficial','official_url'],['subRenew','renew_url'],['subAdmin','admin_url'],['subIp','ip_info'],['subNotes','notes'],['subProvider','provider_name'],['subTotalGB','traffic_total_gb'],['subUsedGB','traffic_used_gb']])
      byId(id).value=sub?.[key]??'';
    byId('subRole').value=sub?.usage_role||'unassigned';
    showDialog('subscriptionEditor');
  }
  byId('addSubscription').onclick=()=>editSubscription();
  byId('subscriptionForm').onsubmit=async event=>{
    event.preventDefault();
    const payload={id:state.editingSub?.id||'sub-'+crypto.randomUUID(),update_source:'手动填写',usage_role:byId('subRole').value};
    for(const [id,key] of [['subName','name'],['subExpiry','expire_date'],['subPurpose','purpose'],['subOfficial','official_url'],['subRenew','renew_url'],['subAdmin','admin_url'],['subIp','ip_info'],['subNotes','notes'],['subProvider','provider_name'],['subTotalGB','traffic_total_gb'],['subUsedGB','traffic_used_gb']])
      payload[key]=byId(id).value.trim();
    byId('saveSubscription').disabled=true;
    try{
      await request('subscriptions/'+(state.editingSub?'update':'create'),payload);
      byId('subscriptionEditor').close();await loadSubscriptions();toast('订阅已保存');
    }catch(error){byId('subFormError').textContent=error.message;}
    finally{byId('saveSubscription').disabled=false;}
  };
  // Recalculate date-only countdowns across midnight without querying any external provider.
  let subscriptionDay=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});
  setInterval(()=>{const day=new Date().toLocaleDateString('en-CA',{timeZone:'Asia/Shanghai'});if(day!==subscriptionDay){subscriptionDay=day;loadSubscriptions().catch(()=>{});}},60000);

  function indicator(id,text,status){const target=byId(id);target.textContent=text;target.className='wb-indicator '+status;}
  function renderExit(data){let label='待查询';if(data.checked)label=data.region?(new Intl.DisplayNames(['zh-CN'],{type:'region'}).of(data.region)||data.region):'待确认';indicator('exitIndicator','出口：'+label,data.checked&&data.region?'good':'warn');byId('exitIndicator').title=data.detail+(data.checked?' · '+new Date(data.checked*1000).toLocaleString():'');byId('publicIp').textContent='出口 IP：'+(data.ip||'待查询');byId('subLiveIp').textContent='出口 IP：'+(data.ip||'待查询');byId('publicIp').title=data.detail||'点击刷新查询';byId('networkLatency').textContent='连接耗时：'+(Number.isFinite(data.latency_ms)?data.latency_ms+' ms':'待查询');}
  async function localStatus(){try{const data=await request('status');indicator('clashIndicator','Clash：'+({running:'运行中',stopped:'未运行',unknown:'待确认'}[data.clash]||'待确认'),data.clash==='running'?'good':data.clash==='stopped'?'bad':'');byId('clashIndicator').title='Clash 进程与代理端口独立检测；代理端口：'+(data.proxy==='connected'?'连通':'断开');renderExit(data.exit);byId('subLiveClash').textContent='Clash：'+({running:'运行中',stopped:'未运行',unknown:'待确认'}[data.clash]||'待确认');const r=data.traffic;const nice=n=>n>=1048576?(n/1048576).toFixed(1)+' MB/s':n>=1024?(n/1024).toFixed(0)+' KB/s':n+' B/s';byId('networkTraffic').textContent=r?'实时流量 ↓'+nice(r.download_bps)+' ↑'+nice(r.upload_bps):'实时流量：待采样';byId('subLiveTraffic').textContent=r?'本机 ↓'+nice(r.download_bps)+' ↑'+nice(r.upload_bps):'本机流量：待采样';}catch{indicator('clashIndicator','Clash：待确认','');byId('clashIndicator').title='本地网络状态暂不可用';}}
  async function refreshNetwork(){if(state.refreshing)return;state.refreshing=true;byId('refreshNetwork').disabled=true;try{await localStatus();renderExit(await request('status/refresh-exit',{}));}catch(error){toast(error.message);}finally{state.refreshing=false;byId('refreshNetwork').disabled=false;}}
  byId('refreshNetwork').onclick=refreshNetwork;byId('refreshPulse').onclick=refreshNetwork;
  byId('toggleFullscreen').onclick=async()=>{
    try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}
    catch{toast('系统阻止了全屏切换，可使用窗口最大化按钮');}
  };
  document.addEventListener('fullscreenchange',()=>{byId('toggleFullscreen').textContent=document.fullscreenElement?'⛶ 退出全屏':'⛶ 全屏';});
  window.addEventListener('claude-state',event=>{const s=event.detail;const [label,status]=s.offline?['服务离线','warn']:s.sampling?['采样中','warn']:s.busy?['检测中','warn']:s.changed?['需重检','warn']:s.failed?['需处理','bad']:s.expired?['已过期','warn']:s.ready?['可手动启动','good']:s.report||s.error?['待核验','warn']:['待检测',''];indicator('claudeIndicator','Claude：'+label,status);});
  window.Workbench={request,state,toast,showDialog,showClaude,switchView,loadItems,loadSubscriptions,editItem,editSubscription,appIcon,searchKeys};
  reloadItems();loadSubscriptions().catch(error=>{byId('subscriptionSummary').textContent='订阅：暂不可用';byId('subscriptionCards').replaceChildren(el('p','wb-empty',error.message));});refreshNetwork();
  setInterval(localStatus,10000);
})();
