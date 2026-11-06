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
| QQ 小号（NapCat OneBot 11，@ 触发、去重、重连） | 完成（已真机验收） |
| 图片/表情接收（落盘到 `inbox/`，只有表情不带字也会回） | 完成 |
| 图片压缩 + 视觉描述（长边 1024，按哈希缓存） | 完成 |
| 媒体元数据表（类型/哈希/OCR/描述/TTL） | 完成 |
| 文档理解（QQ 文件 → pdf/docx/xlsx/pptx/txt，预览入会话） | 完成 |
| 动图理解（抽取 6 帧拼图 + 一句描述） | 完成 |
| 表情合成（`/表情` 文字卡片或已有表情做底图） | 完成 |
| 表情包（内置 15 张 DeepSeek 鲸鱼娘，`[表情:键名]` 触发） | 完成 |
| 语音消息（SILK/AMR → 本地 whisper 转写） | 完成 |
| 戳一戳（连回 3 次后冷却 5 秒） | 完成 |
| 主动提醒（`/remind`，跨重启保留） | 完成 |
| 任务规划—执行—校验（`/plan`，多步任务自动触发） | 完成 |
| 主动巡检（任务卡住 / 磁盘 / inbox，`/patrol`） | 完成 |
| 梗库与斗图（`/梗`、`/斗图 开`，按群限流） | 完成 |
| 群聊被动上下文（被 @ 时带上最近发言） | 完成 |
| 长期记忆（本地向量召回 + 时效衰减 + 画像去重） | 完成 |
| 反馈学习（纠正自动归纳为规则，`/rules` 查看） | 完成 |
| 图片 OCR（截图/报错图先本地认字） | 完成 |
| 最近图片上下文（后续提问可引用刚才那张图） | 完成 |
| 语音回复（`/voice`，edge-tts → SILK） | 完成 |
| 只读实例（禁 shell，读取限制在工作目录） | 完成 |
| ACP 本地 agent（`/do`，权限审批 y/n） | 完成 |
| OpenAI 兼容后端 + 无 key 降级 | 完成 |
| 会话记忆 / 摘要压缩 / `/remember` | 完成 |
| 三套人格（私聊/群聊/公开） | 完成 |
| 渲染降级（markdown/长度/按钮） | 完成 |
| 权限判定（默认拒绝/群聊零权限/高危确认） | 完成 |
| 小黑盒 / B站直播 | 未做 |

## 语音与表情

- **图片**：入站图片在 handler 任务里后台下载到 `inbox/日期/`，不挡后续消息；单文件超过 10 MB 直接丢弃。下载后先按魔数纠正扩展名（GIF 不再被当成 JPEG），再用内容哈希查 `media` 表：命中就复用 OCR 和描述，重复图片只留一份。普通图片先跑本地 RapidOCR，识别到的文字以 `[图片文字] ...` 随消息进会话；随后压到长边 1024 的 JPEG，交给视觉模型生成一句 `[图片描述] ...`。最近 3 张图会在 10 分钟内保留（带描述），后续问“刚才那张”时把路径和描述注入提示词。
- **媒体缓存**：`media` 表按 `sha256` 存类型、路径、MIME、OCR、描述和 `expires_at`，默认 7 天（`config.yaml` 的 `media.ttl_days`）；启动和写入时清理过期行。描述用 `media.caption_agent` 指向的 OpenAI 兼容接口，接口不支持图片时本次运行自动停用，不影响聊天。
- **动图**：GIF/动图按时间均匀抽最多 6 帧拼成一张联系表，交给视觉模型概括动作或情绪，以 `[动图] N 帧` + `[动图描述] ...` 进会话；拼图按哈希缓存，同一张动图不会重复抽帧。
- **文档**：QQ 文件消息（`file`）下载到 `inbox/日期/`，按后缀抽取正文：PDF 用 `pypdf`，DOCX 用 `python-docx`，XLSX 用 `openpyxl`，PPTX 用 `python-pptx`，纯文本直接读。预览（默认 3000 字）以 `[文件内容] ...` 进会话，完整文本另存为 `<文件>.txt` 供 agent 读取；单文件默认上限 20 MB，PDF 最多看 30 页。
- **表情合成**：`/表情 好耶` 生成本地文字卡片；`/表情 开心 好耶` 用内置表情做底图并在底部加字。PIL 直接合成，不调模型，输出到 `data/generated/`。
- **梗库**：`/梗 无语` 按键名、标签和说明匹配内置表情，直接把梗图发出来并带一句说明；`/梗` 无参数时列出全部键名。索引来自 `assets/stickers/index.json` 的 `tags`/`alt`。
- **斗图**：群里 `/斗图 开` 后，该群发表情可以不 @ 小号；收到只有表情的消息会按语义匹配回一张，同一张 10 分钟内不重复，每群每小时有上限。设置落 `group_setting`，重启后保留。
- **语音**：QQ 语音先落到工作目录的 `inbox/`，`pilk` 解 SILK 后由本地 `faster-whisper` 转写，文本以 `[语音] ...` 进会话；模型默认放 `models/whisper-small`（已在 `.gitignore`）。没装依赖或模型缺失时自动降级为只标记 `[语音]`。
- **表情**：`assets/stickers/` 内置 15 张 DeepSeek 鲸鱼娘（大肥鱼）表情，文件名即键名（`开心.jpg` → `[表情:开心]`），可另写 `index.json` 的 `aliases`；提示词会带上每张表情的语义说明，命中时支持去标点/唯一包含匹配。只在聊天里触发，正经汇报/报错时不发。素材来自 [fisharchive.cc](https://fisharchive.cc/)，CC BY-NC-SA 4.0，非商用，出处与署名见 `assets/stickers/index.json`。
- **收表情**：对方发来的 QQ 小黄脸（`face`）、商城表情（`mface`）和动画表情（`image` + `sub_type=1`）都会转成 `[对方发来…]` 文本；小黄脸名优先读 NapCat 的 `face_config.json`（280+ 个，缺表时回退内置常用表）。带图的会落到 `inbox/`，按表情专用提示词生成 `[表情含义] ...`，并按哈希缓存；只有表情不带字也会触发回复。
- **语音回复**：`/voice 你好呀` 用 edge-tts 生成中文语音，转成腾讯 SILK 后以 `record` 发回；代理、音色、语速都在 `config.yaml` 的 `tts` 段。
- **戳一戳**：收到 `notice/notify/poke` 后 35% 概率直接回戳对方，其余情况从 `POKE_REPLIES` 随机回一句。同一会话连回 3 次后冷却 5 秒。群里会先 @ 戳的人，不进模型会话。

## 主动与记忆

- **提醒**：`/remind 10m 喝水`、`/remind 明天 8:00 起床`、`/remind 2026-11-06 08:00 开会`；`/remind` 看列表，`/remind cancel <id>` 取消。提醒落 SQLite，重启后仍会补发。
- **中断跟进**：启动时若发现上次进程留下的未完成任务，会主动发一句说明，并把任务标成 `interrupted`。
- **记忆召回**：优先用本地 `BAAI/bge-small-zh-v1.5` 向量，模型缺失时自动退回字级 n-gram + IDF；两者都带时效衰减。画像键会归一化去重（`称呼` / `称呼：` 视为同一个）。OCR 文字和语音转写随消息一起入库并参与向量召回，所以“上次那张报错截图”也能被想起来。
- **群聊上下文**：每个群在内存里保留最近 20 条发言；被 @ 时把最近 12 条连同发言人注入提示词，只用于理解前文，不逐条回应。
- **反馈学习**：私聊里出现纠正/约束语句（“别用列表”“以后都…”）时，用 chat 模型归纳成一条规则，写入 SQLite 并注入后续提示词；`/rules` 查看已学到的规则。
- **静默工具进度**：QQ 等推送渠道不再发送“在弄 xx”，只在 web/cli 显示，减少刷屏和风控。
- **任务规划**：`/plan 任务` 或 `/do` 里出现“然后/分别/整理/排查/修复/部署”等多步词时，先用轻量模型拆成 1–5 步，逐步交给 worker 执行，每步用校验器判断是否通过，失败重试一次，最后汇总；每个步骤的目标、验收标准、尝试次数、证据和时间都落 `task_step` 表。简单任务仍走原来的单轮，避免变慢。
- **主动巡检**：每 10 分钟检查任务卡住、磁盘剩余空间、`inbox` 体积；命中阈值且不在静默时段才推送，同类通知有冷却。`/patrol` 可随时手动查看，不发通知。

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
- 如果最近日志显示 NapCat 正在等待**验证码/扫码**，不自动重启，只发一次带 WebUI 链接的人工验证通知
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
python tests/smoke.py         # 28 项：store/persona/render/permission/router
python tests/qq_e2e.py        # OneBot 渠道（mock 服务端）
python tests/character_test.py  # 角色卡 JSON/PNG/世界书/变量替换
python tests/e2e_http.py      # HTTP 端到端（Web 渠道）
python tests/proactive_test.py  # 提醒解析/投递/中断跟进
python tests/memory_test.py    # 摘要/画像/反馈规则（真调模型）
python tests/tts_test.py       # /voice 指令（不真调 edge-tts）
python tests/acp_e2e.py      # ACP 适配器 + mock agent
python tests/router_acp.py   # /do -> 工具调用 -> 审批 -> 继续
python tests/media_cleanup_test.py  # inbox 过期文件清理
python tests/doutu_test.py   # 梗库检索、斗图开关、频率上限
python tests/task_step_test.py  # 任务步骤状态/重试/中断留档
python tests/watchdog_test.py  # 掉线巡检、重启与验证码拦截
python tests/eval/run.py     # 39 条真实评测：聊天/任务/记忆/主动/玩法/媒体
```

### 真机验收（2026-11-05）

环境：Windows 本机 + NapCat 容器（`napcat`，QQ `1450236275`），服务 `127.0.0.1:8010`。

| 项目 | 结果 | 依据 |
|---|---|---|
| 服务健康 / 渠道加载 | 通过 | `/healthz` 返回 `chat=openai`、`worker/worker_lite=acp`、渠道 `qq_onebot,web` |
| QQ 登录与 WS 连接 | 通过 | 日志：`onebot 已连接`、`登录账号：1450236275` |
| 私聊收发 | 通过 | 17:40–18:31 多轮消息正常往返 |
| 入站图片 + OCR | 通过 | 图片落 `inbox/` 后由 `read_image` 打开并回述内容 |
| 表情发送 | 通过 | 回复后追加 `[图片]` 表情素材 |
| 语音回复 | 通过 | `/voice` 生成后以 `[语音 4s]` 发出 |
| 戳一戳 | 通过 | 17:46/17:47 收到戳后随机回话 |
| 中断任务跟进 | 通过 | 17:57 补发两条被中断任务说明 |
| 掉线重连 / 自动重启 | 部分通过 | 自动重启有效；需短信验证时仍需人工过 WebUI |
| 群聊 @ 触发 | 未真机验收 | 仅 `tests/qq_e2e.py` mock 覆盖 |
| 小黑盒 / B站直播 | 未做 | 渠道开关默认 `false` |

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
app/vision.py      视觉描述（图片 / 表情 / 动图）
app/docs.py        文档文本抽取（pdf/docx/xlsx/pptx）
app/synth.py       表情文字合成（本地 PIL）
app/proactive.py   定时提醒、中断跟进与主动巡检
app/planner.py     任务拆解与逐步校验
app/task_runner.py 多步任务执行（规划 → 执行 → 校验 → 重试）
app/embed.py       本地中文向量（记忆召回）
app/ocr.py         本地图片文字识别（RapidOCR）
app/tts.py         语音合成（edge-tts → SILK）
app/agent/         openai / echo / acp / unavailable
app/channel/       web / qq_onebot
```
