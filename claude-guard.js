// IPDESK_GUARD_BEGIN
// Keep this wrapper after the original script, including its early-return paths.
const ipdeskOriginalMain = main;
main = function(config, profileName) {
  config = ipdeskOriginalMain(config, profileName);
  const group = "🛡️ 纯净住宅IP";
  const groups = config["proxy-groups"] || [];
  const target = groups.find(g => g.name === group);
  if (!target || !Array.isArray(target.proxies) || target.proxies.length !== 1 ||
      ["DIRECT", "REJECT"].includes(target.proxies[0])) {
    // Never break subscription generation if its groups change. The launcher
    // will refuse to start until a supported fixed group is restored.
    return config;
  }
  // Preserve process mode, DNS, TUN, proxies and all other global settings.
  const processes = "PROCESS-NAME-REGEX,(?i)^claude(?:-code)?\\.exe$";
  const path = "PROCESS-PATH-REGEX,(?i)[\\\\/](?:AnthropicClaude|Claude)(?:_[^\\\\/]+)?[\\\\/]";
  const domains = ["claude.ai", "claude.com", "anthropic.com", "claudeusercontent.com",
    "claudemcpcontent.com", "claude.app", "clau.de"];
  const rules = [];
  for (const match of [processes, path]) {
    // Local MCP/tools remain reachable. Do not send LAN traffic to the ISP proxy.
    for (const cidr of ["127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"]) {
      rules.push(`AND,((${match}),(IP-CIDR,${cidr},no-resolve)),DIRECT`);
    }
    rules.push(`AND,((${match}),(IP-CIDR6,::1/128,no-resolve)),DIRECT`);
    // The fixed HTTP proxy cannot carry UDP. Explicitly reject instead of fallback.
    rules.push(`AND,((${match}),(NETWORK,UDP)),REJECT`);
    rules.push(`${match},${group}`);
  }
  for (const domain of domains) {
    rules.push(`AND,((DOMAIN-SUFFIX,${domain}),(NETWORK,UDP)),REJECT`);
    rules.push(`DOMAIN-SUFFIX,${domain},${group}`);
  }
  config.rules = [...rules, ...(config.rules || []).filter(r => !rules.includes(r))];
  return config;
};
// IPDESK_GUARD_END
