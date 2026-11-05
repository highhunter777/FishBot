# 角色卡（Character Card）

> 支持 V2 / V3，JSON 与 PNG 内嵌两种形态。放进 `config/characters/`，配置里指一行路径即可。

---

## 1. 怎么用

```yaml
# config/config.yaml
agents:
  chat:
    character: config/characters/xiaoyu.json   # 或 .png
```

也可以用 `/chat` 走这个后端验证。

字段优先级：**配了 `character` 就用卡**，没配才用 `config/prompts/<scope>/*.md`。

---

## 2. 支持的格式

| 形态 | 说明 |
|---|---|
| `*.json`（V2） | `{"spec":"chara_card_v2","data":{...}}` |
| `*.json`（V3） | `{"spec":"chara_card_v3","data":{...}}`，按 V2 字段读 |
| `*.json`（V1） | 顶层直接是 `name/description/...` 也认 |
| `*.png` | 从 `tEXt` 块取 `chara`（V2）或 `ccv3`（V3），base64 JSON |

PNG 解析是手写的分块读取，**不依赖 Pillow**；`app/character/card.py` 里的 `write_png()` 也能反向生成一张自检卡。

### 字段映射

| 角色卡字段 | 去向 |
|---|---|
| `system_prompt` | 整段设定最前面，空则用内置一句 |
| `description` / `personality` / `scenario` | 依次进入【设定】【性格】【场景】 |
| `mes_example` | 进【对话示例，照着这个语气说】—— **对人设影响最大的一段** |
| `post_history_instructions` | 放最后，优先级最高 |
| `character_book.entries` | 世界书，按关键词命中 |
| `first_mes` / `alternate_greetings` | 已解析；当前不自动开场（按需接） |

### 变量

`{{char}}`、`{{user}}`、`{{persona}}`、`{{bot}}`，以及 V1 的 `<BOT>` / `<USER>`。
`{{user}}` 取自当前 scope 下 profile 里的「称呼」，没记就是「你」。

---

## 3. 世界书（lorebook）

每条 entry：

| 字段 | 作用 |
|---|---|
| `keys` | 命中词，任意一个出现在本轮用户消息里就注入（不区分大小写） |
| `content` | 注入的文本 |
| `constant` | 常驻，不看关键词 |
| `insertion_order` | 升序拼接 |
| `enabled` | 关闭则跳过 |

总预算 1200 字，超了就丢弃靠后的条目，避免把上下文撑爆。

实测：问「明天适合钓鱼吗」→ 命中「野钓点」条目；问「今天天气不错」→ 只留常驻条目。

---

## 4. 两个真实坑

### 网关会把 system 角色丢掉

本机 `127.0.0.1:4000` 那个转发，实测：

```
system 角色:      未遵守 -> '我无法获取实时天气数据…'      ← 设定被忽略
user 内嵌:        遵守   -> '喵喵'
合并首轮 + 重申:   遵守   -> '喵喵'
```

三个模型（deepseek-v4.1-flash / glm-5.3-flash / gpt-6-luna）表现一致，说明是**网关不透传 system**，不是模型问题。

对策：`chat.system_in_user: true` —— 把设定合并进最后一条用户消息。换成正经 API 后关掉即可。

这也解释了一个现象：ACP 通道的人设一直有效（`<context>` 本来就拼在用户消息里），而 chat 通道失效。

### 旧历史会压制人设

换卡之后如果不清理历史，模型会延续之前的助手口吻（实测第一次换卡后仍然满口「您可以告诉我…」）。

换卡时执行 `/new`，或清对应 scope：

```sql
DELETE FROM message WHERE channel='cli' AND scope='chat';
```

---

## 5. 自检

```bash
python tests/character_test.py     # 22 项：V2 解析 / PNG 内嵌 / 变量 / 世界书 / 组装
```

---

## 6. 想扩展

| 需求 | 改动 |
|---|---|
| 自动发开场白 | `character.first_message()` 已实现，接在会话首轮即可 |
| 按群绑定不同角色 | `AgentCfg.character` 改成按 `conv_id` 查表 |
| 多角色切换 | 加 `/persona <名>` 指令，维护一份名字到卡片的映射 |
| 世界书递归扫描 | `lorebook.select` 目前只看本轮用户消息，可再扫最近 N 轮 |
| 支持 V3 的 assets | V3 多了图片资源，当前只读文本字段 |
