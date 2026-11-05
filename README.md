# FishBot

多渠道个人助手：QQ 小号 / Web 聊天页，后端可换（OpenAI 兼容 或 本地 ACP agent）。

架构见 `docs/architecture.md`，类人与权限见 `docs/persona-and-permissions.md`。

## 跑起来

```bash
pip install -r requirements.txt
copy .env.example .env      # 填 LLM_KEY；不填走 echo 演示后端
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Web：http://127.0.0.1:8010 ｜ 命令行验证：`python -m app.cli "在吗"`

## 接 QQ 小号（NapCat / OneBot 11）

1. 启动 Docker Desktop，跑 NapCat：

```bash
docker run -d --name napcat --restart=always \
  -p 3000:3000 -p 3001:3001 -p 6099:6099 \
  -e NAPCAT_GID=0 -e NAPCAT_UID=0 \
  mlikiowa/napcat-docker:latest
```

2. 打开 `http://127.0.0.1:6099/webui`（默认密码 `napcat`，**立刻改**），扫码登录小号
3. WebUI → 网络配置 → 新建 **WebSocket 服务器**，端口 `3001`，设一个 token
4. `.env` 写 `NAPCAT_TOKEN=<同一个 token>`
5. `config/config.yaml` 里把 `channels.qq.enabled` 改成 `true`
6. 重启服务，私聊小号发一条消息；日志里会打印来源 QQ 号
7. 把这个 QQ 号填进 `allowed_users`，之后只有你能用（留空时只服务私聊，群聊一律不理）

群里默认**必须 @ 小号**才响应（`group_requires_at: true`）。

## 接本地 agent（dsh 桌面端）

`/do <任务>` 会交给 ACP agent。默认复用本机 **DeepSeek Harness 桌面端**内置的 dsh-acp：

```yaml
worker:
  adapter: acp
  command:
    - "E:\\DeepSeekHarness\\DeepSeek Harness.exe"
    - "E:\\DeepSeekHarness\\resources\\app.asar\\dsh\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js"
    - "acp"
  env:
    ELECTRON_RUN_AS_NODE: "1"
  cwd: workspace
```

要点：
- 桌面端主程序以 Node 模式跑 asar 内的 CLI，profile 用内置的 `acp`，共享 `~/.dsh` 的会话
- **不用官方 key**：`dsh_refs: true` 会自动把 `~/.dsh/.credentials.yaml` 里的 refs（`OPENCODE_GO_API_KEY` / `OPENCODE_API_KEY` / `ME_API_KEY`）注入子进程环境
- `config/dsh-acp.patch.yml` 注册三套 provider，默认用 `opencode-go` + `deepseek-v4.1-flash`（与桌面端一致），另备 `opencode`、`me`（本机 `127.0.0.1:4000` 网关）
- 换 provider 要改**两处** `provider: / model:`（`agent-default-model` 和 `acp`）—— **只改前者不生效**，ACP 会话认的是 `acp` 那份
- `cwd` 是 agent 的活动范围，越窄越安全
- 换后端只改这一块，例如 `["cmd", "/c", "npx", "-y", "@zed-industries/codex-acp@latest"]`

想换成别的 provider，改 `dsh-acp.patch.yml` 里的 `providers` 与两处 `provider: / model:` 即可。

验证：

```bash
python tests/acp_real.py                    # 真实握手 + 一轮对话
python tests/acp_probe.py <命令...>          # 探测任意 ACP agent
```

## 当前能力

| 能力 | 状态 |
|---|---|
| Web 聊天页（流式 + 打字节奏） | 完成 |
| QQ 小号（NapCat OneBot 11，@ 触发、去重、重连） | 完成（待真机联调） |
| 图片收发（落到 agent 工作目录的 `inbox/`，单文件 10 MB 上限） | 完成 |
| 表情包（内置 15 张 DeepSeek 鲸鱼娘，`[表情:键名]` 触发） | 完成 |
| 语音消息（SILK/AMR → 本地 whisper 转写） | 完成 |
| 戳一戳（连回 3 次后冷却 5 秒） | 完成 |
| 只读实例（禁 shell，读取限制在工作目录） | 完成 |
| ACP 本地 agent（`/do`，权限审批 y/n） | 完成 |
| OpenAI 兼容后端 + 无 key 降级 | 完成 |
| 会话记忆 / 摘要压缩 / `/remember` | 完成 |
| 三套人格（私聊/群聊/公开） | 完成 |
| 渲染降级（markdown/长度/按钮） | 完成 |
| 权限判定（默认拒绝/群聊零权限/高危确认） | 完成 |
| 小黑盒 / B站直播 | 未做 |

## 语音与表情

- **图片**：入站图片在 handler 任务里后台下载到 `inbox/日期/`，不挡后续消息；单文件超过 10 MB 直接丢弃。
- **语音**：QQ 语音先落到工作目录的 `inbox/`，`pilk` 解 SILK 后由本地 `faster-whisper` 转写，文本以 `[语音] ...` 进会话；模型默认放 `models/whisper-small`（已在 `.gitignore`）。没装依赖或模型缺失时自动降级为只标记 `[语音]`。
- **表情**：`assets/stickers/` 内置 15 张 DeepSeek 鲸鱼娘（大肥鱼）表情，文件名即键名（`开心.jpg` → `[表情:开心]`），可另写 `index.json` 的 `aliases`；只在聊天里触发，正经汇报/报错时不发。素材来自 [fisharchive.cc](https://fisharchive.cc/)，CC BY-NC-SA 4.0，非商用，出处与署名见 `assets/stickers/index.json`。
- **戳一戳**：收到 `notice/notify/poke` 后从 `app/persona.py` 的 `POKE_REPLIES` 里随机回一句；同一会话连回 3 次后冷却 5 秒，冷却结束重新计数。群里会先 @ 戳的人，不进模型会话。

## 服务守护

三层，从内到外：

| 层 | 实现 | 管什么 |
|---|---|---|
| 进程内巡检 | `app/watchdog.py` | QQ 掉线连续 6 分钟 → 自动 `docker restart napcat`，仍不行再弹窗；恢复再弹一条 |
| 外部看门狗 | `scripts/supervise.ps1` + 计划任务「FishBot Supervisor」 | 每 2 分钟探 `/healthz`，挂了自动重启并通知 |
| 登录态持久化 | `data/napcat/qq` 挂载卷 | 容器/电脑重启后用 `ACCOUNT` 快速登录，不用重扫 |

看门狗行为：

- **服务健康时静默退出**，不写日志、不弹窗（所以每 2 分钟跑一次不会打扰你）
- 连续 2 次探活失败（4 分钟）才动手，避免瞬时抖动
- 10 分钟内重启过会跳过，防止反复重启
- 重启后仍不可用 → 弹窗提示人工介入

进程内巡检与它互补：

- QQ WS 连续 3 次不健康（约 6 分钟）→ 自动重启 NapCat 容器；同一轮掉线只重启一次，10 分钟后才允许下一次
- 自动重启后仍连不上 → 弹窗提示人工扫码/过 WebUI 验证
- 频道重连退避上限 20 秒，NapCat 恢复后很快接上

### 免扫码重启（密码回退登录）

NapCat 支持 `ACCOUNT` + `NAPCAT_QUICK_PASSWORD_MD5`：先试快速登录，失败就用密码兜底，
**容器重启不需要扫码**。实测日志：

```
正在快速登录 1450236275
快速登录错误： 登录态已失效，请重新登录。
正在尝试密码回退登录 1450236275
密码回退登录成功 1450236275
WebSocket服务: 0.0.0.0:3001, : 已启动
```

容器重建时必须带这两个环境变量（`docker update` 改不了 env，只能重建）：

```powershell
docker rm -f napcat
docker run -d --name napcat --restart=on-failure:3 `
  -p 3000:3000 -p 3001:3001 -p 6099:6099 `
  -e NAPCAT_GID=0 -e NAPCAT_UID=0 `
  -e ACCOUNT=1450236275 `
  -e NAPCAT_QUICK_PASSWORD_MD5=d4f217f2f9c94d497042553c27e6c89b `
  -v "e:/FishBot/data/napcat/config:/app/napcat/config" `
  -v "e:/FishBot/data/napcat/qq:/app/.config/QQ" `
  mlikiowa/napcat-docker:latest
```

- `--restart=on-failure:3` 是**熔断**：密码也登不上时最多重启 3 次就停，避免反复无效登录被风控冻号
- MD5 在 `docker inspect` 里可见且可被彩虹表反查（弱密码等同明文），**建议换强密码**
- 风控要求设备验证/验证码时密码登录也会失败，仍会退回二维码——这条路只能降低概率，不能保证

登录自启放在**启动文件夹**（`FishBot.vbs`），而不是 ONLOGON 计划任务——
后者需要管理员权限，启动文件夹不需要：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FishBot.vbs
```

登录后它静默调用 `supervise.ps1 -Force`：服务已在跑就直接退出，没跑就拉起来。

相关命令：

```powershell
Get-ScheduledTask -TaskName "FishBot Supervisor"        # 看状态
Start-ScheduledTask -TaskName "FishBot Supervisor"      # 立刻跑一次
Unregister-ScheduledTask -TaskName "FishBot Supervisor" -Confirm:$false   # 取消守护
Get-Content data/supervise.log -Tail 10                 # 守护日志
```

> 注意：PowerShell 5.1 读无 BOM 的 UTF-8 脚本会把中文当 GBK 解析，直接语法报错。
> `scripts/*.ps1` 必须保存为 **UTF-8 with BOM**，改动后记得检查。

## agent 权限边界

**真实边界由 dsh 自己的沙盒决定，不是 `app/permission.py`。**

实测：dsh 不实现 ACP 的 `session/request_permission`（日志零条），`new_session` 也不返回
`modes`，所以 `permission.py` 那套分级对它是死代码——只有换上实现了该请求的后端
（如 claude-agent-acp）才会逐工具调过来。

按人分两层：

```yaml
allowed_users: ["你", "朋友的号"]   # 能聊天
owner_users:   ["你"]               # 能让它动手（shell / 文件）
```

非 owner 发普通消息 → 走 `worker_lite`：`read-only` 沙盒，禁 `bash/pwsh/jobs`，`read`/`read_image`
只允许工作目录内，`glob/grep` 禁用；发 `/do` → 直接回「这个只有主人能用」。
本机渠道（`web` / `cli`）不受名单限制。`owner_users` 留空则等同 `allowed_users`。

| 控制点 | 位置 | 说明 |
|---|---|---|
| `DSH_PERMISSION_MODE` | `worker.env` | `read-only` / `workspace-write` / `danger-full-access`，**这是真正的开关** |
| 工作目录 | `worker.cwd` | `C:/Users/hunter/FishBot-workspace`。放 E 盘时 dsh 补沙盒 ACL 会失败（`SetNamedSecurityInfoW Win32 5`），pwsh 直接跑不起来 |
| 注入的密钥 | `worker.dsh_refs` | 传列表只注入需要的：`["OPENCODE_GO_API_KEY"]`。传 `true` 会把所有 refs 都塞进 agent 环境 |
| 渠道白名单 | `allowed_users` | 非本人消息在 Router 层就丢，不进 agent |
| 只读实例 | `worker_lite` | 禁 shell；`fs-read-guard` 把 `read`/`read_image` 限在 cwd，禁 `glob/grep` |
| 读取围栏插件 | `config/dsh-plugins/fs-read-guard.mjs` | 通过 `dsh-acp.patch.yml` 的 `insert` 挂载，只在 `read-only` 生效 |
| 群聊工具权限 | `permission.evaluate` | 群聊一律 DENY（对支持 ACP 权限的后端生效） |

验证方式：

```bash
python -m app.cli "/do 用 pwsh 执行 Get-Location"
python -m app.cli "/do 检查 ME_API_KEY 是否为空"
```

## 测试

```bash
python tests/smoke.py        # 28 项：store/persona/render/permission/router
python tests/qq_e2e.py       # OneBot 渠道（mock 服务端）
python tests/acp_e2e.py      # ACP 适配器 + mock agent
python tests/router_acp.py   # /do -> 工具调用 -> 审批 -> 继续
```

## 目录

```
app/domain.py      跨层契约
app/config.py      config.yaml + .env
app/store.py       SQLite（message/session/profile/task）
app/session.py     人格、上下文、摘要
app/persona.py     节奏、分段、去 AI 味
app/render.py      按渠道能力降级
app/permission.py  工具权限判定
app/approval.py    审批闸门（默认拒绝）
app/router.py      白名单/节流/指令/选后端/首响/审批
app/pump.py        Push 渠道事件泵
app/stt.py         语音转写（SILK → 文本）
app/stickers.py    表情标记解析与素材索引
app/agent/         openai / echo / acp / unavailable
app/channel/       web / qq_onebot
```
