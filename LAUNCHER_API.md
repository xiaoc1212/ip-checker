# 启动台阶段 1：后端接口

阶段 1 完成日期：2026-11-06。本文记录基础后端接口；阶段 2 主界面及新增接口已完成，见 [PHASE2.md](PHASE2.md)。软件库在阶段 3 实现。

## 存储与初始化

- `launcher_store.py`：统一模型、验证、排序、线程锁、原子 JSON 写入。
- `launcher_service.py`：Windows 本机启动、浏览器个人资料、单例窗口激活、已知客户端初次定位。
- `launcher_api.py`：新增接口、同源与请求体校验。
- `launcher-data.json`：首次运行时创建；包含 7 个分组、17 个预设入口、2 张订阅卡。已加入 Git 忽略。
- 初始化仅在首次创建配置时定位已知客户端，不会重置后来编辑的配置，也不会启动软件。完整开始菜单扫描在阶段 3。
- 文件损坏时不自动覆盖；新接口返回 503，原 Claude 页面和检测接口仍可用。
- 保存使用同目录临时文件、flush/fsync 与原子替换。多线程读写加锁；只支持一个后端实例共同维护同一配置文件。

## 地址与来源验证

全部新接口的实际前缀为 `http://127.0.0.1:<动态端口>/<会话令牌>/api/`。下表省略此会话部分，页面可用 `fetch('./api/launcher/items')` 请求。

仅监听 127.0.0.1。新接口验证连接地址、Host、同端口的 Origin/Referer 和 Sec-Fetch-Site；不提供跨站 CORS 放行。Host 接受 127.0.0.1 或 localhost，但 Origin 必须与该请求 Host 完全一致。GET 至少携带匹配的 Origin 或当前会话 Referer；POST 必须带匹配 Origin，若另带 Referer 也必须匹配。原检测页面继续使用原地址及原验证规则。

POST 必须为 `application/json`，最大 64 KiB。启动接口严格只接收 `{"id":"..."}`，不会执行请求体传来的路径或命令。增删改接口只修改本地配置，不执行目标；删除不删除原文件。

## 接口清单

| 方法 | 路径 | 请求 / 结果 |
|---|---|---|
| GET | `/api/launcher/items` | 返回 `{groups, items}`，数组顺序即保存顺序 |
| POST | `/api/launcher/open` | `{id}`，从本地配置查找目标并打开 |
| POST | `/api/launcher/items/create` | 新项目配置；最少 `{id, name}`，默认 projects 分组和空网页目标 |
| POST | `/api/launcher/items/update` | `{id, ...要修改的字段}`；target_config 支持局部修改 |
| POST | `/api/launcher/items/delete` | `{id}` |
| POST | `/api/launcher/items/reorder` | `{ids:[全部项目ID]}`；拒绝漏项与重复 |
| GET | `/api/subscriptions` | 返回 `{subscriptions}`，含动态到期状态 |
| POST | `/api/subscriptions/create` | 最少 `{id, name}` |
| POST | `/api/subscriptions/update` | `{id, ...要修改的字段}` |
| POST | `/api/subscriptions/delete` | `{id}` |
| POST | `/api/subscriptions/reorder` | `{ids:[全部订阅ID]}` |

新增返回 201，其余成功返回 200；配置保存返回 `{ok:true,item:配置}`，删除和排序返回 `{ok:true}`。失败返回 `{code,error,...必要修复信息}`。

`last_used`（Unix 秒）、`use_count` 为后端维护字段，创建/编辑时不传；订阅 `remaining_days`、`expiry_status` 只读，也不传回保存接口。浏览器/profile 的修改示例：

```json
{"id":"gemini","target_config":{"browser":"msedge","profile":"Profile 1"}}
```

## 启动行为

- web：仅允许 HTTP/HTTPS；默认浏览器经系统关联打开，指定浏览器则以参数数组调用，不拼接 shell 命令。Profile 为文件夹名，不是磁盘路径。网页禁止单例进程设置。
- exe：仅本机绝对 EXE 路径；启动前检查文件及工作目录。参数通过 Windows 原生 CommandLineToArgvW 解析，再以 `shell=False` 启动。
- folder：只允许存在的本机文件夹。
- uwp：校验 AUMID 格式后通过系统 explorer 的 AppsFolder 启动；本阶段不通过启动请求证明应用安装、登录或最终窗口已就绪。
- 非浏览器客户端可设 singleton_process；检测到已运行时尝试恢复/激活窗口，Windows 拒绝前台切换或没有可见窗口则返回 already_running，避免重复启动。
- claude_protected：只返回 `{ok:true,status:"requires_check",action:"open_claude_drawer",id}`。该响应不代表客户端已启动，不计入成功使用次数。原 `POST launch` 继续独立处理完整预检后的手动启动。
- 禁止将已有 Claude 保护入口改成普通入口；识别到直接指向 Claude.exe / claude-code.exe 或 Claude AUMID 的新增普通项也拒绝保存。

成功发送打开请求或激活已有客户端后更新使用记录，不代表应用已完成联网或登录。执行失败不更新记录；打开成功但记录保存失败时返回成功及 history_saved=false 和 warning，避免提示用户重复启动。

常用状态：400 参数错误，403 来源拒绝，404 ID/接口不存在，409 配置或安装需修复，413 体积超限，415 请求类型错误，503 存储/启动不可用。

目标缺失返回 `target_missing` 和 `official_url`、`actions`，供阶段 2 显示“重新定位/编辑”和官网入口，不会擅自打开下载页。静态 IP 后台和 VPN 网站没有准确资料时留空。

## 订阅日期

按北京时间的自然日计算，不依赖出口 API。日期为空时 remaining_days=null、expiry_status=unfilled；过去日期为 expired；今天为 due_today；1–3 天 urgent；4–7 天 warning；其余 normal。没有自动同步实现，不会编造到期日期或联网失败时判过期。

## 原功能保护与验证

`network.py`、`diagnostics.py`、`dns_audit.py`、HTML/CSS/JS 与本阶段开始时的文件哈希一致；Dashboard 类内容一致。HTTP Handler 提取为可测试的工厂，仅增加新 API 分发，退出操作使用 `self.server.shutdown` 指向当前服务；原 check/state/launch/connections/dns-audit/quit 数据契约保留。

测试覆盖真实本机 HTTP 请求、Host/Origin/Referer 拒绝、ID 白名单、原启动门禁、日期边界、CRUD/排序、并发计数、原子保存失败、配置损坏隔离和模拟 Windows 启动。运行 `python -m pytest -q`。测试数据保存在 reports/launcher-tests 独立目录，所有软件/网页启动均模拟，不操作真实客户端。

预设网页来源：[Google Flow](https://labs.google/fx/tools/flow)、[Antigravity](https://antigravity.google/)、[ChatGPT 下载](https://chatgpt.com/download/)、[Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev)。Windows 启动参考：[CommandLineToArgvW](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-commandlinetoargvw)、[应用 AUMID](https://learn.microsoft.com/en-us/windows/configuration/store/find-aumid)。
