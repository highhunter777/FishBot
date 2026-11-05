# 人格、记忆与权限（按后端隔离）

> 核心约定：**chat 与 agent 是两个人**。人格文件分开、记忆分开、权限不同。

---

## 1. 三条隔离线

| 维度 | chat（闲聊） | worker（干活） |
|---|---|---|
| 人格 | `config/prompts/chat/*.md` | `config/prompts/worker/*.md` |
| 记忆 scope | `chat` | `worker` |
| 谁维护历史 | 我们（SQLite） | agent 自己（ACP session）+ 我们记账 |
| 工具权限 | 无 | 分级（见第 4 节） |
| 默认入口 | `/chat <内容>` | 直接说话（默认） |

`scope` 由 Router 传下去：`scope = agent_name`，一路带到 `store` 的每条 SQL。

---

## 2. 人格：按目录分

```
config/prompts/
├─ chat/    private.md  group.md  public.md     # 闲聊：像熟人，短
├─ worker/  private.md  group.md  public.md     # 干活：先结论，动手前说清
└─ private.md  group.md  public.md              # 兜底（旧结构，缺目录时用）
```

查找顺序：`prompts/<scope>/<scene>.md` → `prompts/<scene>.md` → 内置默认。
`scene` 由渠道决定：私聊 `private`、群聊 `group`、直播/公开 `public`。

改文案直接改文件，重启生效（有进程内缓存）。

---

## 3. 记忆：按 scope 落库

```
message 表： scope | channel | conv_id | user_id | role | content | ts
             ^^^^^ chat / worker，索引第一列
session 表： key = "<scope>|<channel>|<conv_id>"  → summary
profile 表： user_id = "<scope>:<uid>"            → {"称呼": "老王"}
task 表：    scope 记录这轮是哪个后端跑的
```

- 旧库自动补 `scope` 列，旧数据归到 `chat`（不会丢，但也不会串进 agent）
- `profile` 读不到时会回退查一次裸 `user_id`（兼容迁移前的数据）

### 实测

```
$ /remember 称呼=老王           → 记下了（worker）。
$ /chat 我叫什么？              → 不知道
$ /do 我叫什么？                → 老王
```

### agent 首轮拿到的上下文

ACP 只收一段文本，所以 `_compose` 把 messages 里的 **system 段**（人格 + 关于对方 + 早前摘要）包进 `<context>` 放在首轮消息前，之后同一 session 不再重复注入（历史由 ACP 自己维护）。

---

## 3.5 会话记忆归 agent（跨重启恢复）

分工：**会话内发生过什么，交给 agent 自己记**；我们只在 SQLite 里存一张映射表。

```
agent_session 表：scope | session_key | session_id | updated_at
```

流程：

1. Router 打开会话前查 `agent_session`，把上次的 id 放进 `meta["resume_session_id"]`
2. `AcpAdapter.open()` 先试 `session/resume`（dsh 的会话存在 `~/.dsh/sessions/*.jsonl`）
3. 恢复失败就 `session/new`，成功后把新 id 写回表
4. `/new` 同时清 SQLite 上下文**和**这条映射

实测（两个独立进程）：

```
$ python -m app.cli "/do 记住这个数字：42。只回收到两个字"
收到。

$ python -m app.cli "/do 我刚才让你记住的数字是多少？只回数字"
42
```

代价：**恢复的会话不会重新注入人格与 profile**（历史已经在 agent 手里）。所以新记了一条事实之后，要 `/new` 让新会话带着新 profile 重建。这是有意的取舍——每轮重灌 profile 会翻倍烧 token，而 profile 变更并不频繁。

---

## 3.6 后台记忆维护（LLM 摘要 + 事实抽取）

每轮回复写库之后，起一个后台任务（不阻塞回复）：

```
remember() → create_task(_maintain)
               ├─ 条数 > 40 → 丢最早 20 条 → 让模型压缩成事实记录
               └─ 私聊 → 让模型抽取关于用户的长期事实 → 写 profile
```

用的是 `chat` 那套模型（便宜快），不占用 agent 会话。同一会话加锁，避免并发压缩打架。

### 摘要：从规则拼接 → LLM

改之前是把丢弃的消息截前 60 字拼起来。现在的输出：

```
双方连续讨论部署方案细节（第0至26条），但未产生任何具体方案内容、结论或决定，
也未明确偏好。此前已聊过部署。部署方案细节仍未完成，需继续讨论。
```

有因果、有「未完成」、能合并旧摘要。模型调用失败会自动退回规则拼接。

### 事实抽取

只抽长期有效的（称呼、身份、长期偏好、在做的项目、明确的规矩），不抽临时状态。
实测输入「我叫老王，在做 FishBot 这个项目，以后都叫我老王」：

```json
{"称呼": "老王", "正在做的项目": "FishBot", "明确说过的规矩": "以后都叫我老王"}
```

输出容错：去 markdown 代码块、去前后废话、单点失败返回 None。

### 配置

```yaml
memory:
  enabled: true
  agent: chat            # 用哪个后端的模型
  summarize: true
  extract_facts: true
```

关掉任意一项即退回原行为。没有可用 key 时整体静默降级。

---

## 3.7 相关性召回

目标：三个月前说过的偏好，现在问起来能想起来，而不是只看最近 12 轮。

### 为什么不是向量召回

两个候选端点都不支持 embedding：

```
opencode 端点 /embeddings -> 404
本机 4000 网关 /v1/embeddings -> 404
```

所以用**字级 n-gram + IDF**，零依赖、离线可用。中文同时切单字与 2-gram，
英文按词；命中靠 IDF 加权，除以文档长度做归一。

### 一个必须理解的取舍：精度优先

最初不加限制时，`今天吃什么` 会靠单字「天」「吃」召回「爬山/香菜」两条，
而正确命中「钓鱼 → 野钓点」的分数（0.57）还低于误召回的（0.60）—— 单字层面
分不开主题相关和偶然重合。

现在的规则：**至少有一个 2-gram 或英文词重合才算候选**，单字只参与加分。

| 查询 | 结果 |
|---|---|
| `那个野钓点在哪` | 命中「城北水库野钓点」 |
| `Docker 部署搞定了吗` | 命中「周五前跑通 Docker」 |
| `钓鱼`（只有单字重合） | **不召回** |
| `今天吃什么` | 不召回 |

代价：同义改写召回不到（「钓鱼」找不到只说「野钓」的句子）。要突破这层只能上 embedding。

### 注入方式

命中结果作为一条 system 消息插在摘要之后、历史之前：

```
可能相关的旧对话：
- 上次说的那个野钓点在城北水库，水深大概三米
```

最多 5 条，每条截 180 字。模型自己会判断相关性，无关就忽略。

### 配置

```yaml
memory:
  recall: true
  recall_k: 5
```

---

## 4. 权限分级

在 `app/permission.py`，ACP 的每次 `session/request_permission` 都会先过一遍：

| 情形 | 判定 | 表现 |
|---|---|---|
| 只读工具（read/list/search/grep/fetch…） | `ALLOW` | 静默放行，不打扰你 |
| 写 / 执行（write/edit/shell/run…） | `ASK` | 转成聊天里的「要执行 X，回 y/n」 |
| 高危（`rm -rf`、`git push -f`、`mkfs`、`curl | sh`…） | `ASK` | 文案标「（高危）」 |
| 未知工具 | `ASK` | 默认不信任 |
| 群聊 / 直播 / 非私聊 | `DENY` | 直接拒，连问都不问 |
| 非白名单用户 | `DENY` | 同上 |
| `read_only: true` 时的一切写操作 | `DENY` | 直接拒，agent 只能读 |

**当前默认 `read_only: true`** —— 先给低权限，agent 只能看不能改。
要放开改成 `false`，届时写操作会走 y/n 审批。

审批超时 180 秒，超时按拒绝处理；回复不明确（不是 y/n）会提示重发。

---

## 5. 边界

1. **跨 ACP session 的细节会丢**。重启后 agent 只记得 profile + 摘要，不记得上次 `ls` 出什么。
2. **摘要是规则拼接**，不是 LLM 总结：丢弃的消息截前 60 字拼起来，上限 800 字。长对话会失真，改 `SessionService._compact` 可升级。
3. **profile 要手动记**（`/remember`）。想做自动抽取，在 `remember()` 后加一次轻量调用。
4. **人格对 agent 是「建议」**。`<context>` 属于用户消息，agent 理论上可不遵守；要强约束得 patch dsh 的 `system-prompt` 插件。
5. **群聊不按群隔离 profile**。profile 按 `scope + user_id` 全局，同一个人在不同群共享偏好。要隔离就把 key 加上 `channel:conv_id`。

---

## 6. 想改的话改哪里

| 需求 | 改动 |
|---|---|
| 默认回到闲聊 | `config.yaml` 的 `default_agent: chat` |
| 放开写权限 | `worker.read_only: false` |
| 让 agent 每轮都带完整历史 | `AcpAdapter.prompt` 去掉 `_fresh` 判断 |
| 摘要改用 LLM | `SessionService._compact` |
| 自动抽取用户事实 | `SessionService.remember` 后加一次抽取 |
| 按群隔离记忆 | `Store._profile_key` 加上 `channel:conv_id` |
| 强化人格约束 | patch `@deepseek-ai/dsh-system-prompt` |
