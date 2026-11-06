# 软件库、场景与桌面接口

继承原会话前缀 `http://127.0.0.1:<port>/<token>/api/`，下表省略该前缀。继续校验本机连接、Host、Origin/Referer。执行接口不接收前端磁盘路径或命令。

| 方法 | 路径 | 请求 / 结果 |
|---|---|---|
| GET | `scanner/apps` | 缓存软件库，含候选 ID、名称、类型、图标和重复项 ID |
| POST | `scanner/refresh` | `{}`，重新扫描 |
| POST | `scanner/parse-shortcut` | `{name,data}`，500KB 内快捷方式 base64 内容；不执行 |
| POST | `scanner/pick-shortcuts` | `{}`，原生多选，不解引用 .lnk，返回解析预览 |
| POST | `scanner/pin` | `{ids,group,pinned,allow_duplicate}`，候选 ID 原子批量添加 |
| GET | `icons/<sha256>.png` | 本地透明 PNG 图标 |
| GET | `workflows` | 场景列表与当前队列 |
| POST | `workflows/save` | `{id,name,item_ids,require_proxy}` |
| POST | `workflows/delete` | `{id}`，只删场景配置 |
| POST | `workflows/start` | `{id}`，执行已保存场景；同一时间一个队列 |
| POST | `workflows/cancel` | `{}`，停止后续步骤 |
| GET | `desktop/status` | 托盘/热键注册结果、唤醒计数、必要提示 |
| POST | `desktop/wake` | `{}`，唤醒当前会话窗口或重新打开同一地址 |
| GET | `backup/export` | ZIP 字节，配置、场景与引用图标 |
| POST | `backup/preview` | `{data}`，base64 ZIP 校验后返回暂存 ID 与数量 |
| POST | `backup/restore` | `{id}`，恢复已预览备份，保留恢复前 ZIP |

候选有效期 1 小时，备份预览 10 分钟。快捷方式上传体积含编码最多 700KB，解码后最多 500KB；备份文件与解压内容总计分别不超过 20MB。恢复拒绝目录穿越、未知文件、重复条目、无效图标和危险目标，不使用 extractall。PNG 文件名匹配内容 SHA256。

统一类型仍为 web/exe/uwp/folder/claude_protected。web 除 HTTP/HTTPS 外，仅允许 `steam://rungameid/<数字>`、`steam://run/<数字>`、`steam://open/main`，通过系统关联打开，不允许额外命令或浏览器 Profile。官网与订阅链接仍只允许 HTTP/HTTPS。

场景 waiting_claude 只能由原有 POST launch 成功后的服务端通知解除，没有前端可调用的绕过接口。原启动轮次、目标、规则、连接异常、有效期检查和响应结构保留。

启动项与场景独立原子保存。批量添加一次提交。备份恢复持有数据与场景锁，先验证、保存原文件，再应用；失败还原原始字节。数据损坏时原 Claude 页面仍可使用，并保留有效备份预览与恢复能力。

launcher_shell.py 隔离系统 Shell COM 解析；launcher_icons.py 仅读取图标资源并渲染。扫描不自动固定全部软件，仅补齐仍为空且名称保持默认的 ChatGPT/Clash 预设目标。

launcher_desktop.py 使用 Shell_NotifyIcon、RegisterHotKey、原生窗口恢复和 AppUserModelID 校验；命名互斥锁防止后端重复运行。退出时移除托盘、注销热键并取消队列。

Microsoft 参考：[文件选择窗口](https://learn.microsoft.com/en-us/windows/win32/dlgbox/open-and-save-as-dialog-boxes)、[应用 AUMID](https://learn.microsoft.com/en-us/windows/configuration/store/find-aumid)、[通知区](https://learn.microsoft.com/en-us/windows/win32/shell/notification-area)。
