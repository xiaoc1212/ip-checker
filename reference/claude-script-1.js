
// ─── geoip-batch interceptor (transparent batching for /api/geoip/{IP}) ───
// Coalesces concurrent fetches within a 20ms window into a single /api/geoip-batch call.
// Falls back to original fetch on batch failure. Zero changes to call sites.
(function(){
  const _origFetch = window.fetch.bind(window);
  const _q = new Map();
  let _scheduled = false;
  function _mkResp(ok, data){ return {ok: ok, status: ok?200:502, json: async ()=>data||{}}; }
  async function _flush(){
    _scheduled = false;
    if (_q.size === 0) return;
    const queueCopy = new Map(_q);
    _q.clear();
    const ips = Array.from(queueCopy.keys());
    if (ips.length === 1){
      // Single IP — original endpoint is fine (cached by CF anyway)
      const ip = ips[0];
      try {
        const r = await _origFetch('/api/geoip/' + ip);
        const data = r.ok ? await r.json() : null;
        for (const cb of queueCopy.get(ip)) cb(_mkResp(r.ok, data));
      } catch (e) {
        for (const cb of queueCopy.get(ip)) cb(_mkResp(false, null));
      }
      return;
    }
    // Multiple IPs — batch
    try {
      const r = await _origFetch('/api/geoip-batch?ips=' + ips.join(','));
      if (!r.ok) throw new Error('batch ' + r.status);
      const data = await r.json();
      for (const [ip, callbacks] of queueCopy) {
        const ipData = data[ip] || null;
        for (const cb of callbacks) cb(_mkResp(!!ipData, ipData));
      }
    } catch (e) {
      // Fallback: individual fetches in parallel
      const tasks = Array.from(queueCopy.entries()).map(async ([ip, callbacks]) => {
        try {
          const r = await _origFetch('/api/geoip/' + ip);
          const data = r.ok ? await r.json() : null;
          for (const cb of callbacks) cb(_mkResp(r.ok, data));
        } catch {
          for (const cb of callbacks) cb(_mkResp(false, null));
        }
      });
      await Promise.all(tasks);
    }
  }
  window.fetch = function(url, opts){
    if (typeof url === 'string') {
      const m = url.match(/^\/api\/geoip\/([0-9a-fA-F.:]+)$/);
      if (m) {
        const ip = m[1];
        return new Promise(resolve => {
          if (!_q.has(ip)) _q.set(ip, []);
          _q.get(ip).push(resolve);
          if (!_scheduled) {
            _scheduled = true;
            setTimeout(_flush, 20);
          }
        });
      }
    }
    return _origFetch(url, opts);
  };
})();

// State
const state = {
  ip: null,
  ippure: null,
  ipapis: null,
  geo: null,
  source: { main: null, security: null, cacheHit: false },
};

// ===== Flag helper =====
// ===== IPv6 helpers =====
function isIPv6(ip) { return ip && ip.includes(':'); }
function truncateIP(ip) {
  if (!isIPv6(ip) || ip.length <= 20) return ip;
  return ip.substring(0, 18) + '...';
}
function displayIP(ip) {
  if (!ip) return '获取失败';
  if (isIPv6(ip)) {
    return `<span class="ip-truncate ip-mask-target" title="${ip}">${truncateIP(ip)}</span>`;
  }
  return `<span class="ip-mask-target">${ip}</span>`;
}
// Wrap an IP in a link to its dedicated /ip/{ip} page, opening in a new tab.
// Purely presentational — inherits all surrounding font styles; hover adds
// a subtle underline (see .ip-link CSS). Used by the three hero IP cards.
function linkIP(ip) {
  if (!ip) return '获取失败';
  const inner = displayIP(ip);
  return `<a class="ip-link" href="/ip/${encodeURIComponent(ip)}" target="_blank" rel="noopener">${inner}</a>`;
}
// ===== Geo text with overflow-aware tooltip =====
function setGeoText(elId, text) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.innerHTML = '<span class="geo-text"></span>';
  const span = el.firstElementChild;
  span.textContent = text || '';
  el.classList.remove('truncated');
  el.removeAttribute('data-full');
  if (!text) return;
  requestAnimationFrame(() => {
    if (span.scrollWidth > span.clientWidth + 1) {
      el.dataset.full = text;
      el.classList.add('truncated');
    }
  });
}
window.addEventListener('resize', () => {
  ['ipGeoCN', 'ipGeo', 'ipGeoClaude'].forEach(id => {
    const el = document.getElementById(id);
    const span = el && el.firstElementChild;
    if (!span || !span.classList || !span.classList.contains('geo-text') || !span.textContent) return;
    const truncated = span.scrollWidth > span.clientWidth + 1;
    el.classList.toggle('truncated', truncated);
    if (truncated) el.dataset.full = span.textContent;
    else el.removeAttribute('data-full');
  });
});
function showIPv6Warning() {
  document.getElementById('ipv6Warn').style.display = 'block';
}

function flagImg(cc) {
  if (!cc) return '';
  const src = cc === 'cn' ? '/favicons/cn.png' : cc.toLowerCase() === 'tw' ? '/favicons/flags/tw.png' : `/favicons/flags/${cc.toLowerCase()}.png`;
  const altTxt = cc.toLowerCase() === 'tw' ? '中国台湾省' : cc;
  return `<img src="${src}" width="40" height="27" style="width:24px;height:auto;border-radius:2px;vertical-align:middle" alt="${altTxt}" onerror="this.onerror=null;this.style.display='none'">`;
}

// ===== Fetch Cloudflare IP via trace =====
async function fetchCfIP() {
  try {
    const r = await fetch('https://1.1.1.1/cdn-cgi/trace', { signal: AbortSignal.timeout(5000) });
    const txt = await r.text();
    const m = txt.match(/ip=([^\n]+)/);
    if (m) state.ip = m[1].trim();
  } catch {}
}

// ===== Score label =====
// Restricted regions where Claude blocks/heavily restricts access.
// Single source of truth — used by both Trust Score render and
// Claude availability check. Add new ISO-2 codes here only.
const CLAUDE_RESTRICTED_CC = {
  'CN': '中国大陆', 'HK': '香港', 'MO': '澳门',
  'RU': '俄罗斯', 'KP': '朝鲜', 'IR': '伊朗', 'SY': '叙利亚',
  'CU': '古巴', 'BY': '白俄罗斯', 'VE': '委内瑞拉'
};
function getRestrictedName() {
  const cc = (state.claudeRisk && state.claudeRisk.countryCode || '').toUpperCase();
  return cc ? (CLAUDE_RESTRICTED_CC[cc] || null) : null;
}

function scoreLabel(trustScore) {
  if (trustScore >= 95) return { text: '极度纯净', cls: 'tag-safe' };
  if (trustScore >= 80) return { text: '纯净', cls: 'tag-safe' };
  if (trustScore >= 50) return { text: '良好', cls: 'tag-info' };
  if (trustScore >= 25) return { text: '中性', cls: 'tag-warn' };
  return { text: '可疑', cls: 'tag-danger' };
}

function boolTag(val, trueText, falseText) {
  if (val === true) return `<span class="tag tag-danger">${trueText || '是'}</span>`;
  if (val === false) return `<span class="tag tag-safe">${falseText || '否'}</span>`;
  return `<span class="tag tag-neutral">未知</span>`;
}

// ===== Render =====
function render() {
  const p = state.ippure;
  const cr = state.claudeRisk;
  const cg = state.claudeGeo;
  const a = state.ipapis;

  // Risk cards — use Claude IP data
  const asn = cr?.asn || p?.asn || '';
  const asnOrg = cr?.asOrganization || p?.asOrganization || a?.company?.name || '';

  // ipAddr/ipGeo already rendered by Cloudflare IP card task — don't overwrite here

  // Restricted regions check — uses module-level CLAUDE_RESTRICTED_CC dict.
  const restrictedName = getRestrictedName();

  // Trust Score — from backend (computed per CIDR, cached 365 days).
  // 三种特殊情形：
  //   a) Claude 出口 IP 没拿到 → 显示 '—' / 无数据
  //   b) 后端无评分数据         → 显示 '—' / 无数据
  //   c) IP 位于受限地区         → 强制 0 分（即使后端算法说 IP 干净）
  const hasClaudeIp = !!(cr && cr.ip);
  let trustScore;
  let restrictedText = null;
  if (!hasClaudeIp) {
    trustScore = null;
  } else if (restrictedName) {
    trustScore = 0;
    restrictedText = '处于不可访问区域，Claude 会严格风控';
  } else {
    trustScore = (typeof cr.trust_score === 'number') ? cr.trust_score : null;
  }
  const gaugePointer = document.getElementById('gaugePointer');
  if (trustScore === null) {
    document.getElementById('gaugeScore').innerHTML = `<span style="color:#9ca3af">—</span> <span class="tag tag-neutral" style="font-size:0.5em;vertical-align:middle">无数据</span>`;
    gaugePointer.style.left = '0%';
    gaugePointer.style.opacity = '0.3';
    document.getElementById('gaugeText').textContent = hasClaudeIp ? '该 IP 暂无评分数据' : '未获取到 Claude AI 出口 IP，无法评分';
  } else {
    const sl = restrictedName ? { text: '不可访问', cls: 'tag-danger' } : scoreLabel(trustScore);
    const scoreColor = trustScore >= 95 ? '#22c55e' : trustScore >= 80 ? '#16a34a' : trustScore >= 50 ? '#84cc16' : trustScore >= 25 ? '#eab308' : '#ef4444';
    document.getElementById('gaugeScore').innerHTML = `<span style="color:${scoreColor}">${trustScore}</span> <span class="tag ${sl.cls}" style="font-size:0.5em;vertical-align:middle">${sl.text}</span>`;
    gaugePointer.style.left = Math.min(trustScore, 100) + '%';
    gaugePointer.style.opacity = '1';
    document.getElementById('gaugeText').textContent = restrictedText
      || (trustScore >= 95 ? '该IP信誉极好' : trustScore >= 80 ? '该 IP 信誉优异' : trustScore >= 50 ? '该 IP 存在轻微风险' : trustScore >= 25 ? '该 IP 存在一定风险' : '该 IP 风险极高');
  }

  // Region restriction warning — Claude AI blocks or heavily risk-controls
  // sessions originating from these regions. Show red banner under the
  // Trust Score so users don't burn an account by trying to log in.
  (function(){
    // Reuse RESTRICTED + restrictedName declared above for trust score logic
    const el = document.getElementById('regionWarn');
    if (!el) return;
    if (restrictedName) {
      el.innerHTML = '<span class="icon">⚠️</span>当前 Claude 出口 IP 位于'
        + '<strong>' + restrictedName + '</strong>，'
        + '不建议尝试登录 Claude，容易触发封号风控！';
      el.style.display = 'block';
    } else {
      el.style.display = 'none';
    }
  })();

  // Claude 支持地区指示行（在 gauge 下方、与右卡 ASN 行视觉齐平）
  // 三态：受限地区 → 整行隐藏（已有红色横幅警告，避免重复提示）
  //       未拿到 IP 或 countryCode → 未知（灰）
  //       拿到完整数据且非受限 → 正常（浅绿，同"未检测到泄露"风格）
  (function(){
    const row = document.getElementById('claudeRegionSupportRow');
    const val = document.getElementById('claudeRegionSupport');
    const filler = document.querySelector('.trust-score-card .trust-score-filler');
    const spacer = document.querySelector('.trust-score-card .trust-score-spacer');
    if (!row || !val) return;
    const hasIp = !!(cr && cr.ip);
    const hasCC = !!(cr && cr.countryCode);
    if (restrictedName) {
      // 受限地区：隐藏整行 + 同时折叠 filler/spacer，避免左卡底部出现大块留白
      row.style.display = 'none';
      if (filler) filler.style.display = 'none';
      if (spacer) spacer.style.display = 'none';
    } else {
      // 非受限：恢复 filler/spacer（让 CSS 媒体查询继续负责桌面/移动差异）
      if (filler) filler.style.display = '';
      if (spacer) spacer.style.display = '';
      if (!hasIp || !hasCC) {
        val.innerHTML = '<span class="tag tag-neutral">未知</span>';
      } else {
        val.innerHTML = '<span class="tag tag-safe">正常</span>';
      }
      row.style.display = '';
    }
  })();

  // IP Properties
  const isResidential = cr?.isResidential ?? (a ? !a.is_datacenter : null);
  const isBroadcast = cr?.isBroadcast ?? p?.isBroadcast ?? null;
  const companyType = a?.company?.type || '';

  // Region + City
  const regionStr = cg?.country || cr?.country || '';
  const cityStr = cg?.city || cr?.city || cg?.country || cr?.country || '';

  let propTag = '';
  if (isResidential === true) propTag = '<span class="tag tag-safe">家庭住宅IP</span>';
  else if (isResidential === false) propTag = '<span class="tag tag-warn">机房IP</span>';
  else propTag = '<span class="tag tag-neutral">未知</span>';
  if (companyType) {
    const typeMap = { 'hosting': 'Hosting', 'isp': 'ISP', 'business': 'Business', 'education': 'Education' };
    propTag += ` <span style="color: var(--text-muted);font-size:0.82em">(${typeMap[companyType] || companyType})</span>`;
  }

  document.getElementById('propsContent').innerHTML = `
    <div class="risk-row"><span class="risk-label">地区</span><span class="risk-value">${regionStr || '未知'}</span></div>
    <div class="risk-row"><span class="risk-label">城市</span><span class="risk-value">${cityStr || '未知'}</span></div>
    <div class="risk-row"><span class="risk-label">IP 属性</span><span class="risk-value">${propTag}</span></div>
    <div class="risk-row"><span class="risk-label">ASN</span><span class="risk-value">${asn ? 'AS' + asn : '未知'}</span></div>
    <div class="risk-row"><span class="risk-label">运营商</span><span class="risk-value" style="font-size:0.85em">${asnOrg || '未知'}</span></div>
  `;

  // Security detection
  const sec = a || {};
  document.getElementById('securityContent').innerHTML = `
    <div class="risk-row"><span class="risk-label">VPN</span><span class="risk-value">${boolTag(sec.is_vpn, 'VPN', '未检测到')}</span></div>
    <div class="risk-row"><span class="risk-label">代理 (Proxy)</span><span class="risk-value">${boolTag(sec.is_proxy, '代理', '未检测到')}</span></div>
    <div class="risk-row"><span class="risk-label">Tor</span><span class="risk-value">${boolTag(sec.is_tor, 'Tor', '未检测到')}</span></div>
    <div class="risk-row"><span class="risk-label">机器人 (Crawler)</span><span class="risk-value">${boolTag(sec.is_crawler, '是', '否')}</span></div>
    <div class="risk-row"><span class="risk-label">滥用记录</span><span class="risk-value">${boolTag(sec.is_abuser, '有记录', '无记录')}</span></div>
  `;

}

// ===== Fetch Claude IP (via claude.ai /cdn-cgi/trace, same as main page split tunnel) =====
async function fetchClaudeIP() {
  try {
    const r = await fetch('https://claude.ai/cdn-cgi/trace', {
      cache: 'no-store',
      signal: AbortSignal.timeout(8000),
    });
    const text = await r.text();
    const entries = Object.fromEntries(text.trim().split('\n').map(l => l.split('=')));
    return { ip: entries.ip || null, loc: entries.loc?.toLowerCase() || null };
  } catch {
    return null;
  }
}

// ===== Fetch CN IP (same sources as main page: ip138 + ip.cn) =====
async function fetchCNIP() {
  // Try ip138
  try {
    const r = await fetch('https://2026.ip138.com/', { signal: AbortSignal.timeout(5000) });
    const html = await r.text();
    const m = html.match(/(\d+\.\d+\.\d+\.\d+)/);
    if (m) return { ip: m[1], source: 'iP138.com' };
  } catch {}
  // Try ip.cn
  try {
    const r = await fetch('https://my.ip.cn/', { signal: AbortSignal.timeout(5000) });
    const html = await r.text();
    const m = html.match(/(\d+\.\d+\.\d+\.\d+)/);
    if (m) return { ip: m[1], source: 'IP.cn' };
  } catch {}
  return null;
}

// ===== Render a single IP card =====
async function renderIPCard(elId, geoElId, ip, locHint) {
  if (!ip) {
    document.getElementById(elId).textContent = '获取失败';
    document.getElementById(geoElId).textContent = '';
    return;
  }
  if (isIPv6(ip)) showIPv6Warning();
  // Show IP immediately with loc hint
  document.getElementById(elId).innerHTML = `${locHint ? flagImg(locHint) : ''} ${linkIP(ip)}`;
  // Fetch geo in background
  try {
    const r = await fetch(`/api/geoip/${ip}`, { signal: AbortSignal.timeout(5000) });
    if (r.ok) {
      const g = await r.json();
      const geo = [g.country, g.region, g.city, g.isp].filter(Boolean).join(' ');
      const cc = g.country_code || locHint || '';
      document.getElementById(elId).innerHTML = `${flagImg(cc)} ${linkIP(ip)}`;
      setGeoText(geoElId, geo);
    }
  } catch {}
}

// ===== DNS Leak Detection =====
// Principle: trigger browser DNS resolution via ipleak.net's subdomain system,
// then read back which DNS resolver IPs were seen.
async function detectDNSLeak() {
  const el = document.getElementById('dnsLeakContent');
  const token = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);

  // Step 1: Trigger 2 rounds of DNS resolution via self-hosted authoritative DNS
  for (let i = 1; i <= 2; i++) {
    await new Promise(resolve => {
      const img = new Image();
      const timer = setTimeout(resolve, 2000);
      img.onload = img.onerror = () => { clearTimeout(timer); resolve(); };
      img.src = `https://${token}-${i}.d.ip.net.coffee/pixel.gif?_=${Date.now()}`;
    });
  }

  // Step 2: Wait briefly, then poll results
  await new Promise(r => setTimeout(r, 1500));

  let dnsServers = [];
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await fetch(`/api/dns/result/${token}`, { signal: AbortSignal.timeout(3000) });
      if (r.ok) {
        const data = await r.json();
        dnsServers = data.dns_servers || [];
        if (dnsServers.length > 0) break;
      }
    } catch {}
    if (attempt === 0) await new Promise(r => setTimeout(r, 1500));
  }

  if (dnsServers.length === 0) {
    el.innerHTML = `
      <div class="risk-row"><span class="risk-label">状态</span><span class="risk-value"><span class="tag tag-safe">DNS 加密或未暴露出口</span></span></div>
    `;
    return;
  }

  // Step 3: Get geo for DNS IPs, find abnormal one or pick first normal
  const claudeCountry = (state.claudeRisk?.country || '').toLowerCase();
  const claudeInChina = claudeCountry.includes('china') || claudeCountry.includes('中国');

  // Check all for China DNS
  let showIP = null;
  let isLeaked = false;
  for (const ip of dnsServers) {
    let geo = null;
    try {
      const r = await fetch(`/api/geoip/${ip}`, { signal: AbortSignal.timeout(3000) });
      if (r.ok) geo = await r.json();
    } catch {}
    const cc = geo?.country_code || '';
    const isp = geo?.isp || '';
    const isCN = cc === 'cn';
    if (isCN && !claudeInChina) {
      showIP = { ip, cc, isp, leaked: true };
      isLeaked = true;
      break;
    }
    if (!showIP) {
      showIP = { ip, cc, isp, leaked: false };
    }
  }

  let rows = `<div class="risk-row"><span class="risk-label">状态</span><span class="risk-value">${
    isLeaked
      ? '<span class="tag tag-warn">可能泄露</span>'
      : '<span class="tag tag-safe">未检测到泄露</span>'
  }</span></div>`;

  if (showIP) {
    rows += `<div class="risk-row"><span class="risk-label">DNS 出口</span><span class="risk-value" style="font-size:0.85em">${flagImg(showIP.cc)} <span class="ip-mask-target">${showIP.ip}</span> ${showIP.leaked ? '<span class="tag tag-warn" style="font-size:0.8em">中国DNS</span>' : ''}</span></div>`;
    if (showIP.isp) rows += `<div class="risk-row pc-only"><span class="risk-label">服务商</span><span class="risk-value" style="font-size:0.85em;font-weight:400;color: var(--text-muted)">${showIP.isp}</span></div>`;
  }
  el.innerHTML = rows;
}

// ===== WebRTC UDP Leak Detection =====
async function detectWebRTCLeak() {
  const el = document.getElementById('udpLeakContent');
  const udpIPs = new Set();

  try {
    const pc = new RTCPeerConnection({
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun.cloudflare.com:3478' },
      ]
    });
    pc.createDataChannel('');
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    await new Promise((resolve) => {
      const timeout = setTimeout(() => { pc.close(); resolve(); }, 5000);
      pc.onicecandidate = (e) => {
        if (!e.candidate) { clearTimeout(timeout); pc.close(); resolve(); return; }
        const m = e.candidate.candidate.match(/([0-9]{1,3}\.){3}[0-9]{1,3}/);
        if (m) {
          const ip = m[0];
          if (!ip.startsWith('0.') && !ip.startsWith('127.') && ip !== '0.0.0.0') {
            udpIPs.add(ip);
          }
        }
        // Also check for IPv6
        const m6 = e.candidate.candidate.match(/([a-f0-9]{1,4}:){2,7}[a-f0-9]{1,4}/i);
        if (m6) udpIPs.add(m6[0]);
      };
    });
  } catch {}

  const claudeIp = state.claudeRisk?.ip || '';
  const allUdp = [...udpIPs];
  // Filter: only IPv4 public IPs (skip local/private)
  const publicUdp = allUdp.filter(ip => !isIPv6(ip) && !ip.startsWith('192.168.') && !ip.startsWith('10.') && !ip.startsWith('172.') && !ip.startsWith('198.18.') && !ip.startsWith('198.19.') && !ip.startsWith('100.64.') && !ip.startsWith('127.') && !ip.startsWith('0.'));

  if (publicUdp.length === 0 && allUdp.length === 0) {
    el.innerHTML = `
      <div class="risk-row"><span class="risk-label">状态</span><span class="risk-value"><span class="tag tag-safe">WebRTC 已禁用或无泄露</span></span></div>
    `;
    return;
  }

  if (publicUdp.length === 0) {
    el.innerHTML = `
      <div class="risk-row"><span class="risk-label">状态</span><span class="risk-value"><span class="tag tag-safe">未检测到泄露</span></span></div>
    `;
    return;
  }

  // Leak rule: ANY public UDP IP differing from the Claude exit IP = leak (same as /webrtc/).
  // If exit IP unavailable (race), stay conservative: no leak verdict.
  const leakIP = claudeIp ? publicUdp.find(ip => ip !== claudeIp) : undefined;
  let showIP = leakIP || publicUdp.find(ip => ip === claudeIp) || publicUdp[0];
  const matchesClaude = showIP === claudeIp;
  const isLeaked = !!leakIP;

  let rows = `<div class="risk-row"><span class="risk-label">状态</span><span class="risk-value">${
    isLeaked
      ? '<span class="tag tag-warn">可能泄露</span>'
      : '<span class="tag tag-safe">未检测到泄露</span>'
  }</span></div>`;

  // Fetch geo for the show IP
  let showFlag = '', showCountry = '';
  try {
    const r = await fetch(`/api/geoip/${showIP}`, { signal: AbortSignal.timeout(5000) });
    if (r.ok) { const g = await r.json(); showFlag = g.country_code || ''; showCountry = g.country || ''; }
  } catch {}

  rows += `<div class="risk-row"><span class="risk-label">UDP 出口</span><span class="risk-value" style="font-size:0.88em">${flagImg(showFlag)} ${displayIP(showIP)} ${matchesClaude ? '' : isLeaked ? '<span class="tag tag-warn" style="font-size:0.8em">异常</span>' : ''}</span></div>`;
  if (showCountry) rows += `<div class="risk-row pc-only"><span class="risk-label">归属地</span><span class="risk-value" style="font-size:0.85em;font-weight:400;color: var(--text-muted)">${showCountry}</span></div>`;
  el.innerHTML = rows;
}

// ===== Claude Availability Detection =====
async function detectClaudeAvail() {
  const el = document.getElementById('claudeAvailContent');
  const targets = [
    { name: 'claude.ai', url: 'https://claude.ai/cdn-cgi/trace' },
    { name: 'anthropic.com', url: 'https://www.anthropic.com/cdn-cgi/trace' },
  ];

  const results = await Promise.allSettled(targets.map(async t => {
    const start = performance.now();
    try {
      await fetch(t.url, { mode: 'no-cors', signal: AbortSignal.timeout(6000) });
      return { name: t.name, ms: Math.round(performance.now() - start), ok: true };
    } catch {
      return { name: t.name, ms: -1, ok: false };
    }
  }));

  // 受限地区强制覆盖：即使浏览器侧能联通 claude.ai，Claude 服务对该出口 IP
  // 拒绝服务，从用户视角"事实上不可访问"——不显示误导性 ms 时延。
  const restricted = getRestrictedName();
  let rows = '';
  results.forEach(r => {
    const d = r.value;
    if (restricted) {
      rows += `<div class="risk-row"><span class="risk-label">${d.name}</span><span class="risk-value"><span class="tag tag-danger">不可访问</span></span></div>`;
    } else if (d.ok) {
      const cls = d.ms < 250 ? 'tag-safe-dark' : d.ms < 500 ? 'tag-safe' : 'tag-warn';
      const label = d.ms < 250 ? '正常' : d.ms < 500 ? '良好' : '较慢';
      rows += `<div class="risk-row"><span class="risk-label">${d.name}</span><span class="risk-value"><span class="tag ${cls}">${label}</span> <span style="color: var(--text-muted);font-size:0.85em">${d.ms}ms</span></span></div>`;
    } else {
      rows += `<div class="risk-row"><span class="risk-label">${d.name}</span><span class="risk-value"><span class="tag tag-danger">不可访问</span></span></div>`;
    }
  });

  // Fetch Claude service status from status.json
  try {
    const statusResp = await fetch('/claude/status.json', { signal: AbortSignal.timeout(3000) });
    if (statusResp.ok) {
      const st = await statusResp.json();
      const ind = st.overall_indicator || 'none';
      const indText = { 'none': '全部服务正常', 'minor': '轻微故障', 'major': '重大故障', 'critical': '严重故障', 'maintenance': '维护中' };
      const indCls  = { 'none': 'tag-safe-dark', 'minor': 'tag-warn', 'major': 'tag-danger', 'critical': 'tag-danger', 'maintenance': 'tag-warn' };
      const statusText = indText[ind] || '服务异常';
      const statusCls = indCls[ind] || 'tag-warn';
      rows += `<div class="risk-row"><span class="risk-label">Claude服务状态</span><span class="risk-value"><a href="/claude/status.html" target="_blank" rel="noopener" title="点击查看Claude实时服务状态" style="text-decoration:none"><span class="tag ${statusCls}">${statusText}</span></a></span></div>`;
    }
  } catch {}

  el.innerHTML = rows;
}

// ===== Device Info =====
function renderDeviceInfo() {
  // Timezone comparison: local vs Claude IP timezone (IANA-based, DST-aware)
  const localTz = Intl.DateTimeFormat().resolvedOptions().timeZone || '未知';
  const localOffset = -(new Date().getTimezoneOffset() / 60);
  const localUtc = 'UTC' + (localOffset >= 0 ? '+' : '') + localOffset;

  // Get current-moment UTC offset (minutes) for an IANA timezone name, or
  // null if the name is invalid / unknown. DST is handled by the browser's
  // ICU tzdata — calling this during July vs December gives different
  // answers for "America/New_York".
  function currentOffsetMinutes(tzName) {
    if (!tzName) return null;
    try {
      const parts = new Intl.DateTimeFormat('en-US', {
        timeZone: tzName, timeZoneName: 'longOffset'
      }).formatToParts(new Date());
      const p = parts.find(x => x.type === 'timeZoneName');
      if (!p) return null;
      // "GMT-07:00" or "GMT+5:30" or "GMT" (for UTC)
      if (p.value === 'GMT' || p.value === 'UTC') return 0;
      const m = p.value.match(/GMT([+-])(\d{1,2}):?(\d{0,2})/);
      if (!m) return null;
      const sign = m[1] === '+' ? 1 : -1;
      return sign * (parseInt(m[2], 10) * 60 + parseInt(m[3] || '0', 10));
    } catch (e) { return null; }
  }

  function formatOffsetHours(mins) {
    if (mins == null) return '';
    const sign = mins >= 0 ? '+' : '-';
    const abs  = Math.abs(mins);
    const h = Math.floor(abs / 60);
    const m = abs % 60;
    return 'UTC' + sign + h + (m ? ':' + String(m).padStart(2, '0') : '');
  }

  const cr = state.claudeRisk;
  // Country-code → representative IANA timezone fallback. Used when the IP
  // risk backend hasn't returned an exact `timezone` field (rare IPs, race
  // conditions, upstream timeouts). The mapping deliberately picks the most
  // populous TZ for multi-TZ countries (US: LA, RU: Moscow, CA: Toronto, etc.).
  const CC_TO_TZ = {
    'CN': 'Asia/Shanghai', 'TW': 'Asia/Taipei', 'HK': 'Asia/Hong_Kong', 'MO': 'Asia/Macau',
    'JP': 'Asia/Tokyo', 'KR': 'Asia/Seoul', 'SG': 'Asia/Singapore', 'MY': 'Asia/Kuala_Lumpur',
    'TH': 'Asia/Bangkok', 'VN': 'Asia/Ho_Chi_Minh', 'ID': 'Asia/Jakarta', 'PH': 'Asia/Manila',
    'IN': 'Asia/Kolkata', 'PK': 'Asia/Karachi', 'BD': 'Asia/Dhaka',
    'IR': 'Asia/Tehran', 'IL': 'Asia/Jerusalem', 'AE': 'Asia/Dubai', 'SA': 'Asia/Riyadh',
    'TR': 'Europe/Istanbul', 'RU': 'Europe/Moscow', 'UA': 'Europe/Kyiv',
    'GB': 'Europe/London', 'IE': 'Europe/Dublin', 'FR': 'Europe/Paris', 'DE': 'Europe/Berlin',
    'IT': 'Europe/Rome', 'ES': 'Europe/Madrid', 'PT': 'Europe/Lisbon', 'NL': 'Europe/Amsterdam',
    'BE': 'Europe/Brussels', 'CH': 'Europe/Zurich', 'AT': 'Europe/Vienna',
    'SE': 'Europe/Stockholm', 'NO': 'Europe/Oslo', 'DK': 'Europe/Copenhagen', 'FI': 'Europe/Helsinki',
    'PL': 'Europe/Warsaw', 'CZ': 'Europe/Prague', 'GR': 'Europe/Athens', 'RO': 'Europe/Bucharest',
    'US': 'America/Los_Angeles', 'CA': 'America/Toronto', 'MX': 'America/Mexico_City',
    'BR': 'America/Sao_Paulo', 'AR': 'America/Argentina/Buenos_Aires', 'CL': 'America/Santiago',
    'AU': 'Australia/Sydney', 'NZ': 'Pacific/Auckland',
    'ZA': 'Africa/Johannesburg', 'EG': 'Africa/Cairo', 'NG': 'Africa/Lagos', 'KE': 'Africa/Nairobi'
  };
  const _claudeCC    = (cr?.countryCode || '').toUpperCase();
  // Two-tier: exact timezone from backend > country-derived representative timezone.
  const claudeTz     = (cr?.timezone) || CC_TO_TZ[_claudeCC] || '';
  const tzIsExact    = !!(cr?.timezone);  // true 表示后端给的精确值；false 表示 CC 兜底
  const claudeOffMin = currentOffsetMinutes(claudeTz);
  const localOffMin  = -new Date().getTimezoneOffset();         // minutes
  let   tzMatch      = null;
  if (claudeTz && claudeOffMin != null) {
    const diff = Math.abs(claudeOffMin - localOffMin);
    tzMatch = diff <= 60;  // allow 1h tolerance (neighbor-zone or half-hour offsets)
  }

  const localIsCN = (localTz === 'Asia/Shanghai' || localTz === 'Asia/Urumqi');

  let tzHtml = '';
  if (localIsCN) {
    let claudeLine = 'Claude出口: 未知';
    if (claudeTz && claudeOffMin != null) {
      const claudeOffStr = formatOffsetHours(claudeOffMin);
      const diffHours    = Math.round((claudeOffMin - localOffMin) / 60);
      const diffLabel    = (claudeOffMin - localOffMin) === 0 ? '偏移一致' : diffHours === 0 ? '偏移略有差异' : (diffHours > 0 ? `Claude 快 ${diffHours} 小时` : `Claude 慢 ${-diffHours} 小时`);
      const claudeTzLabel = tzIsExact ? claudeTz : `${claudeTz}（按地区估算）`;
      claudeLine = `Claude出口: ${claudeTzLabel} (${claudeOffStr}) — ${diffLabel}`;
    }
    tzHtml = `<span class="tag tag-warn">不推荐使用中国大陆时区</span><br>`
           + `<span style="font-size:0.85em">本地: ${localTz} (${localUtc})<br>`
           + `${claudeLine}</span>`;
  } else if (tzMatch === true) {
    tzHtml = `<span class="tag tag-safe">时区一致</span> ${localTz} (${localUtc})`;
  } else if (tzMatch === false) {
    const claudeOffStr = formatOffsetHours(claudeOffMin);
    const diffHours    = Math.round((claudeOffMin - localOffMin) / 60);
    const diffLabel    = (claudeOffMin - localOffMin) === 0 ? '偏移一致' : diffHours === 0 ? '偏移略有差异'
                         : (diffHours > 0 ? `Claude 快 ${diffHours} 小时` : `Claude 慢 ${-diffHours} 小时`);
    const claudeTzLabel = tzIsExact ? claudeTz : `${claudeTz}（按地区估算）`;
    tzHtml = `<span class="tag tag-warn">时区不一致</span><br>`
           + `<span style="font-size:0.85em">本地: ${localTz} (${localUtc})<br>`
           + `Claude出口: ${claudeTzLabel} (${claudeOffStr}) — ${diffLabel}</span>`;
  } else {
    tzHtml = `${localTz} (${localUtc})`;
  }

  // Language comparison: local vs Claude IP region's common language
  const localLangs = (navigator.languages || [navigator.language]);
  const langsStr = localLangs.join(', ') || '未知';
  const localPrimary = (localLangs[0] || '').split('-')[0].toLowerCase();

  // Map country (ISO-2) to accepted language codes (multi-language friendly).
  // Key by countryCode for reliability; values are arrays so multilingual
  // regions (Singapore, India, Switzerland, Canada, Belgium...) don't falsely flag.
  const langMap = {
    'CN': ['zh'], 'TW': ['zh'], 'HK': ['zh', 'en'], 'MO': ['zh', 'en'],
    'JP': ['ja'], 'KR': ['ko'], 'TH': ['th'], 'VN': ['vi'],
    'SG': ['en', 'zh', 'ms', 'ta'],
    'MY': ['ms', 'en', 'zh', 'ta'],
    'ID': ['id', 'en'], 'PH': ['en', 'tl', 'fil'],
    'IN': ['en', 'hi'], 'PK': ['ur', 'en'], 'BD': ['bn', 'en'],
    'LK': ['si', 'ta', 'en'], 'NP': ['ne', 'en'],
    'US': ['en'], 'GB': ['en'], 'IE': ['en', 'ga'],
    'AU': ['en'], 'NZ': ['en', 'mi'],
    'CA': ['en', 'fr'],
    'DE': ['de'], 'AT': ['de'],
    'CH': ['de', 'fr', 'it', 'rm'],
    'BE': ['nl', 'fr', 'de'],
    'FR': ['fr'], 'IT': ['it'], 'ES': ['es', 'ca', 'gl', 'eu'],
    'PT': ['pt'], 'NL': ['nl', 'fy'], 'LU': ['lb', 'fr', 'de'],
    'SE': ['sv'], 'NO': ['no', 'nb', 'nn'], 'DK': ['da'],
    'FI': ['fi', 'sv'], 'IS': ['is', 'en'],
    'PL': ['pl'], 'CZ': ['cs'], 'SK': ['sk'], 'HU': ['hu'],
    'RO': ['ro'], 'BG': ['bg'], 'GR': ['el'],
    'RU': ['ru'], 'UA': ['uk', 'ru'], 'BY': ['be', 'ru'],
    'TR': ['tr'], 'IL': ['he', 'ar', 'en'],
    'SA': ['ar'], 'AE': ['ar', 'en'], 'EG': ['ar'],
    'IR': ['fa'], 'IQ': ['ar', 'ku'],
    'ZA': ['en', 'af', 'zu', 'xh'], 'KE': ['en', 'sw'],
    'NG': ['en'], 'ET': ['am', 'en'],
    'BR': ['pt'], 'AR': ['es'], 'MX': ['es'], 'CL': ['es'],
    'CO': ['es'], 'PE': ['es'], 'VE': ['es']
  };
  const cc = (cr?.countryCode || '').toUpperCase();
  const regionLangs = langMap[cc] || [];
  const langMatch = (localPrimary && regionLangs.length)
    ? regionLangs.includes(localPrimary) : null;

  let langHtml = '';
  if (langMatch === true) {
    langHtml = `<span class="tag tag-safe">语言一致</span> ${langsStr}`;
  } else if (langMatch === false) {
    const expected = regionLangs.join(' / ');
    langHtml = `<span class="tag tag-warn">语言不一致</span><br><span style="font-size:0.85em">本地: ${langsStr}<br>Claude出口所在地常用: ${expected}</span>`;
  } else {
    langHtml = langsStr;
  }

  // OS / Browser from UA
  const ua = navigator.userAgent;
  let os = '未知', browser = '未知';
  if (ua.includes('Windows')) os = 'Windows';
  else if (ua.includes('Mac OS')) os = 'macOS';
  else if (ua.includes('iPhone') || ua.includes('iPad')) os = 'iOS';
  else if (ua.includes('Android')) os = 'Android';
  else if (ua.includes('Linux')) os = 'Linux';
  if (ua.includes('Edg/')) browser = 'Edge ' + (ua.match(/Edg\/([\d.]+)/)||[])[1];
  else if (ua.includes('Chrome/')) browser = 'Chrome ' + (ua.match(/Chrome\/([\d.]+)/)||[])[1];
  else if (ua.includes('Firefox/')) browser = 'Firefox ' + (ua.match(/Firefox\/([\d.]+)/)||[])[1];
  else if (ua.includes('Safari/') && !ua.includes('Chrome')) browser = 'Safari ' + (ua.match(/Version\/([\d.]+)/)||[])[1];

  // Touch
  const isTouch = navigator.maxTouchPoints > 0;

  // Network
  const conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const netType = conn ? (conn.effectiveType || conn.type || '未知') : '不支持检测';

  // DNT
  const dnt = navigator.doNotTrack === '1' ? '已开启' : navigator.doNotTrack === '0' ? '已关闭' : '未设置';

  // WebGL Renderer
  let webglRenderer = '不支持';
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (ext) webglRenderer = gl.getParameter(ext.UNMASKED_RENDERER_WEBGL);
    }
  } catch {}

  // WebGL fingerprint hash
  let webglHash = '不支持';
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      const vendor = ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : '';
      const renderer = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : '';
      const str = vendor + '~' + renderer + '~' + gl.getParameter(gl.VERSION) + '~' + gl.getParameter(gl.SHADING_LANGUAGE_VERSION);
      let h = 0;
      for (let i = 0; i < str.length; i++) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
      webglHash = (h >>> 0).toString(16).toUpperCase();
    }
  } catch {}

  // Canvas fingerprint
  let canvasHash = '不支持';
  try {
    const canvas = document.createElement('canvas');
    canvas.width = 200; canvas.height = 50;
    const ctx = canvas.getContext('2d');
    ctx.textBaseline = 'top';
    ctx.font = '14px Arial';
    ctx.fillStyle = '#f60';
    ctx.fillRect(50, 0, 100, 50);
    ctx.fillStyle = '#069';
    ctx.fillText('net.coffee', 2, 15);
    ctx.fillStyle = 'rgba(102,204,0,0.7)';
    ctx.fillText('canvas fp', 4, 30);
    const data = canvas.toDataURL();
    let hash = 0;
    for (let i = 0; i < data.length; i++) {
      hash = ((hash << 5) - hash + data.charCodeAt(i)) | 0;
    }
    canvasHash = (hash >>> 0).toString(16).toUpperCase();
  } catch {}

  document.getElementById('deviceContent').innerHTML = `
    <div class="risk-row"><span class="risk-label">时区</span><span class="risk-value">${tzHtml}</span></div>
    <div class="risk-row"><span class="risk-label">语言</span><span class="risk-value" style="font-size:0.85em">${langHtml}</span></div>
    <div class="risk-row"><span class="risk-label">操作系统 / 浏览器</span><span class="risk-value">${os} / ${browser}</span></div>
    <div class="risk-row"><span class="risk-label">Cookie</span><span class="risk-value">${navigator.cookieEnabled ? '<span class="tag tag-safe">已启用</span>' : '<span class="tag tag-warn">已禁用</span>'}</span></div>
    <div class="risk-row"><span class="risk-label">WebGL 渲染器</span><span class="risk-value dev-long">${webglRenderer}</span></div>
    <div class="risk-row"><span class="risk-label">Canvas 指纹</span><span class="risk-value" style="letter-spacing:1px">${canvasHash}</span></div>
    <div class="risk-row"><span class="risk-label">WebGL 指纹</span><span class="risk-value" style="letter-spacing:1px">${webglHash}</span></div>
  `;
}

// ===== Main — fully parallel, progressive rendering =====
async function main() {
  // Step 1: Get all 3 IPs in parallel
  const [cfResult, cnResult, claudeResult] = await Promise.allSettled([
    fetchCfIP(), fetchCNIP(), fetchClaudeIP()
  ]);

  const cn = cnResult.status === 'fulfilled' ? cnResult.value : null;
  const claude = claudeResult.status === 'fulfilled' ? claudeResult.value : null;

  // Step 2: ALL remaining fetches in parallel — render as they arrive
  const tasks = [];

  // CN IP card
  tasks.push(renderIPCard('ipAddrCN', 'ipGeoCN', cn?.ip, 'cn'));

  // Cloudflare IP card — single fetch, no flash
  tasks.push((async () => {
    if (!state.ip) {
      document.getElementById('ipAddr').textContent = '获取失败';
      document.getElementById('ipGeo').textContent = '';
      return;
    }
    if (isIPv6(state.ip)) showIPv6Warning();
    document.getElementById('ipAddr').innerHTML = linkIP(state.ip);
    try {
      const r = await fetch(`/api/geoip/${state.ip}`, { signal: AbortSignal.timeout(5000) });
      if (r.ok) {
        const g = await r.json();
        const cc = (g.country_code || '').toLowerCase();
        state.ippure = {
          ip: state.ip, country: g.country, countryCode: (g.country_code||'').toUpperCase(),
          region: g.region, city: g.city,
        };
        state.cfGeo = [g.country, g.region, g.city, g.isp].filter(Boolean).join(' ');
        document.getElementById('ipAddr').innerHTML = `${flagImg(cc)} ${linkIP(state.ip)}`;
        setGeoText('ipGeo', state.cfGeo);
      }
    } catch {}
  })());

  // Claude IP card + risk data (the main content)
  tasks.push((async () => {
    const claudeIp = claude?.ip;
    if (!claudeIp) {
      document.getElementById('ipAddrClaude').textContent = '获取失败';
      document.getElementById('ipGeoClaude').textContent = '';
      return;
    }
    // Show Claude IP immediately (truncated if IPv6)
    if (isIPv6(claudeIp)) showIPv6Warning();
    document.getElementById('ipAddrClaude').innerHTML = `${flagImg(claude.loc||'')} ${linkIP(claudeIp)}`;

    // Fetch risk + geo in parallel
    const [riskResp, geoResp] = await Promise.allSettled([
      fetch(`/api/iprisk/${claudeIp}`, { signal: AbortSignal.timeout(10000) }),
      fetch(`/api/geoip/${claudeIp}`, { signal: AbortSignal.timeout(5000) }),
    ]);

    // Process geo (with IPv6 fallback using loc from trace)
    let claudeGeoOk = false;
    if (geoResp.status === 'fulfilled' && geoResp.value.ok) {
      try {
        const g = await geoResp.value.json();
        if (g.country) {
          state.claudeGeo = { country: g.country, region: g.region, city: g.city, isp: g.isp, country_code: g.country_code };
          const geo = [g.country, g.region, g.city, g.isp].filter(Boolean).join(' ');
          document.getElementById('ipAddrClaude').innerHTML = `${flagImg(g.country_code||claude.loc||'')} ${linkIP(claudeIp)}`;
          setGeoText('ipGeoClaude', geo);
          claudeGeoOk = true;
        }
      } catch {}
    }
    // Fallback: use loc code from Cloudflare trace (e.g. "jp", "tw")
    if (!claudeGeoOk && claude.loc) {
      const locMap = {jp:'Japan',tw:'Taiwan',hk:'Hong Kong',sg:'Singapore',us:'United States',de:'Germany',kr:'South Korea',fr:'France',nl:'Netherlands',gb:'United Kingdom',au:'Australia',ca:'Canada',br:'Brazil',in:'India'};
      document.getElementById('ipAddrClaude').innerHTML = `${flagImg(claude.loc)} ${linkIP(claudeIp)}`;
      setGeoText('ipGeoClaude', locMap[claude.loc] || claude.loc.toUpperCase());
    }

    // Process risk
    if (riskResp.status === 'fulfilled' && riskResp.value.ok) {
      const d = await riskResp.value.json();
      state.ipapis = {
        is_datacenter: d.is_datacenter, is_vpn: d.is_vpn, is_proxy: d.is_proxy,
        is_tor: d.is_tor, is_crawler: d.is_crawler, is_abuser: d.is_abuser,
        is_mobile: d.is_mobile, company: { type: d.company_type, name: d.company_name },
        abuser_score: d.abuser_score, datacenter_name: d.datacenter_name,
      };
      state.claudeRisk = {
        ip: claudeIp, asn: d.asn, asOrganization: d.asOrganization,
        country: d.country, countryCode: d.countryCode, region: d.region, city: d.city,
        isResidential: d.isResidential, isBroadcast: d.isBroadcast,
        trust_score: d.trust_score,
        timezone: d.timezone,
      };
    }
  })());

  await Promise.allSettled(tasks);
  render();
  // Run leak detection + availability + device info in parallel (non-blocking)
  Promise.allSettled([detectDNSLeak(), detectWebRTCLeak(), detectClaudeAvail()]);
  renderDeviceInfo();
  saveAndRenderIPHistory();

  // Report IP session for sharing analysis (silent, fire-and-forget)
  try {
    const claudeIp = state.claudeRisk?.ip || '';
    const cfIp = state.ip || '';
    const cnIp = cn?.ip || '';
    if (claudeIp || cfIp || cnIp) {
      fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ claude_ip: claudeIp, cf_ip: cfIp, cn_ip: cnIp }),
      }).catch(() => {});
    }
  } catch {}
}

// ===== IP History (localStorage) =====
const IP_HISTORY_KEY = 'claude_ip_history';
const IP_HISTORY_MAX = 6;

function getIPHistory() {
  try { return JSON.parse(localStorage.getItem(IP_HISTORY_KEY)) || []; } catch { return []; }
}

function saveAndRenderIPHistory() {
  const claudeIp = state.claudeRisk?.ip || '';
  const claudeCC = (state.claudeRisk?.countryCode || '').toLowerCase();
  const claudeGeo = state.claudeRisk?.city || '';

  if (!claudeIp) {
    renderIPHistory();
    return;
  }

  const history = getIPHistory();
  const now = new Date();
  const entry = {
    ip: claudeIp,
    cc: claudeCC,
    geo: claudeGeo,
    time: now.toISOString(),
  };

  if (history.length > 0) {
    const lastEntry = history[0];
    const lastTime = new Date(lastEntry.time);
    const hoursSinceLast = (now - lastTime) / (1000 * 60 * 60);

    // Rule 1: Same IP within 24h → don't record
    if (lastEntry.ip === claudeIp && hoursSinceLast < 24) {
      renderIPHistory();
      return;
    }
    // Rule 2: IP changed → record (regardless of time)
    // Rule 2: Same IP but >24h → record (new day)
  }

  history.unshift(entry);
  if (history.length > IP_HISTORY_MAX) history.length = IP_HISTORY_MAX;
  localStorage.setItem(IP_HISTORY_KEY, JSON.stringify(history));
  renderIPHistory();
}

function renderIPHistory() {
  const el = document.getElementById('ipHistoryContent');
  const history = getIPHistory();

  if (history.length === 0) {
    el.textContent = '暂无历史记录';
    return;
  }

  el.innerHTML = history.map((h, i) => {
    const d = new Date(h.time);
    const timeStr = `${d.getMonth()+1}-${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
    const isCurrent = i === 0;
    return `<div class="risk-row" style="padding:6px 0">
      <span class="risk-label" style="color:${isCurrent ? 'var(--text)' : 'var(--text-muted)'};font-size:0.88em">${timeStr}${isCurrent ? ' <span style="color:#22c55e;font-size:0.82em">(当前)</span>' : ''}</span>
      <span class="risk-value" style="font-size:0.88em">${flagImg(h.cc)} ${linkIP(h.ip)} <span style="color: var(--text-muted);font-size:0.85em">${h.geo}</span></span>
    </div>`;
  }).join('');
}

function clearIPHistory() {
  localStorage.removeItem(IP_HISTORY_KEY);
  renderIPHistory();
}

main();

// ─── 隐藏IP 开关：与主页 / 保持一致 ─────────────────────────────────────
// 默认关；每次刷新都回到关闭状态（不持久化到 localStorage）。
// 通过 textContent 替换只改显示，href 和数据全保留。
function maskIpText(text) {
  if (!text) return '';
  const t = String(text).trim();
  if (/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(t)) {
    const parts = t.split('.');
    return parts[0] + '.' + parts[1] + '.*.*';
  }
  if (t.includes('…')) {
    const idx = t.lastIndexOf('…');
    return t.substring(0, idx + 1) + '*';
  }
  if (t.includes(':')) {
    const parts = t.split(':');
    if (parts.length >= 2) return parts[0] + ':' + parts[1] + ':*';
  }
  return t;
}

window.__ipMaskOn = false;  // 每次刷新默认关闭

function applyMaskToElement(el) {
  if (!el) return;
  if (!el.dataset.realText) {
    el.dataset.realText = el.textContent.trim();
  }
  const real = el.dataset.realText;
  const want = window.__ipMaskOn ? maskIpText(real) : real;
  if (el.textContent !== want) el.textContent = want;
}

window.applyAllIpMasks = function applyAllIpMasks() {
  document.querySelectorAll('.ip-link, .ip-mask-target').forEach(applyMaskToElement);
};

document.addEventListener('DOMContentLoaded', () => {
  const tg = document.getElementById('ipMaskToggle');
  if (tg) {
    tg.checked = false;
    tg.addEventListener('change', (e) => {
      window.__ipMaskOn = e.target.checked;
      document.querySelectorAll('.ip-link, .ip-mask-target').forEach(el => {
        if (!el.dataset.realText) el.dataset.realText = el.textContent.trim();
      });
      applyAllIpMasks();
    });
  }
  applyAllIpMasks();
});

// Auto-apply when IP-bearing containers update (hero cards, history grid, DNS/UDP rows)
(function setupIpMaskObserver(){
  const targets = ['ipHeroCN', 'ipHero', 'ipHeroClaude', 'ipHistoryContent', 'dnsLeakContent', 'webrtcLeakContent'];
  const cb = () => requestAnimationFrame(() => window.applyAllIpMasks && window.applyAllIpMasks());
  const observer = new MutationObserver(cb);
  document.addEventListener('DOMContentLoaded', () => {
    targets.forEach(id => {
      const el = document.getElementById(id);
      if (el) observer.observe(el, { childList: true, subtree: true });
    });
  });
})();


;

/* 最新资讯超过3天未更新则自动隐藏 */
(function(){var el=document.querySelector(".reading-grid a[data-news]");if(!el)return;var d=Date.parse((el.getAttribute("data-news")||"").replace(/-/g,"/"));if(isNaN(d))return;if(Date.now()-d>3*24*60*60*1000)el.style.display="none";})();

