# 设计文档：Automation Builder Copilot

> 这是一份面试题，原则是**最小可用**：把一条主线做扎实，不铺功能。
> 节点词汇的依据见 [wati-node-reference.md](./wati-node-reference.md)。

## 1. 范围

**主线**：用户多轮描述需求 → Agent 生成或修改 flow → 确定性校验 → 用大白话解释 → 用户在模拟器里试跑。

| 做 | 不做（放到 V2） |
|---|---|
| 6 种节点（题目点名的六种）+ 2 种触发方式 | AI 节点、Template、Catalog 等其他节点 |
| 多轮对话：新建、增量修改、追问 | 撤销 / 回滚到历史版本（只保留版本记录） |
| 确定性校验器 | 用 LLM 再审查一遍风险 |
| Agent 生成，外加一次 LLM 调用做解释 | 自动生成测试用例并批量运行 |
| 模拟器（API 调用用 mock 结果） | 真实发出 HTTP 请求、发布上线 |
| 内存存储，单进程 | 持久化、鉴权、多用户 |
| 单页前端：对话 + 节点图 + 模拟器 | 在图上手动编辑节点 |

题目要求的"至少一个 AI 能力"，这里满足两项：**flow 生成**和**大白话解释**。

## 2. 架构

```
frontend/  Vite + React + TS + React Flow
backend/
  app/
    api/        FastAPI 路由，只负责收发请求和转换数据
    domain/     纯确定性代码，不依赖 LLM
      models.py     Flow / Node / Edge / Trigger（Pydantic）
      registry.py   节点注册表：用途、字段、出口规则（提示词和校验都从这里生成）
      edits.py      编辑操作的执行器（整批原子执行）
      validator.py  图校验
      engine.py     模拟执行引擎
      diff.py       两个版本之间的差异
    copilot/    Agent：提示词、工具、有上限的循环、解释
    llm/        对 OpenAI 兼容接口的薄封装
    store.py    内存存储
    config.py   pydantic-settings，读取 .env
  tests/
```

依赖方向：`api → copilot → domain`，`copilot → llm`。`domain` 不知道 LLM 的存在。

**核心原则：LLM 负责提议，确定性代码负责最终决定。** 校验和执行都不经过 LLM。

## 3. 数据模型

### 3.1 Flow

```jsonc
{
  "id": "flow_x",
  "version": 3,
  "name": "Buyer / Seller routing",
  "trigger": { "type": "new_contact_message" },  // 或 { "type": "keyword", "keywords": ["hi"] }
  "start_node_id": "ask_role",
  "nodes": [ /* Node */ ],
  "edges": [ /* Edge */ ]
}
```

### 3.2 节点（6 种）

| type | 字段 | 出口（handle） | 是否可作为终点 |
|---|---|---|---|
| `send_message` | `text` | `next` | 可以（没有出边就结束） |
| `ask_question` | `text`, `mode: buttons \| text`, `options[]`（仅 buttons 模式，1–3 项）, `save_as`（变量名） | buttons 模式：每个选项一个出口 `option:<选项>`，再加一个 `fallback`；text 模式：`next` | 不可以 |
| `condition` | `variable`, `operator: equals \| not_equals \| contains`, `value` | `true` / `false` | 不可以 |
| `api_call` | `method: GET \| POST`, `url`, `body?`, `save_as?` | `success` / `failure` | 不可以 |
| `assign_team` | `team` | `next`（可选） | 可以 |
| `wait` | `duration_seconds`（1 ~ 86400） | `next` | 不可以 |

> 设计取舍：
> - **不设 End 节点**：没有出边就是结束，但只有 `send_message` 和 `assign_team` 允许作为终点，其他节点的出口必须全部连上。这样既少一种节点，校验器也能发现"漏连线"的问题。
> - **Condition 只支持单条规则**，不支持 AND/OR 组合，放到 V2。
> - 选项数量和等待时长的上限是本项目自己定的，不代表 Wati 平台的真实限制。

### 3.3 边

```jsonc
{ "from": "ask_role", "handle": "option:Buyer", "to": "assign_sales" }
```

同一个节点的同一个出口最多只能有一条边，`handle` 必须是该节点类型允许的出口之一。

### 3.4 变量

- 来源：`ask_question.save_as`、`api_call.save_as`，加上联系人内置字段 `contact.name`、`contact.phone`。
- 用法：消息里写 `{{变量名}}`，或者作为 Condition 的 `variable`。
- **什么时候写入变量**（变量写在出口上，不同出口写入的变量不一样）：

  | 节点 / 出口 | 是否写入 `save_as` | 写入的值 |
  |---|---|---|
  | `ask_question` buttons 模式 · `option:<选项>` | 是 | 选项**原文**（见 3.7） |
  | `ask_question` buttons 模式 · `fallback` | **否** | — |
  | `ask_question` text 模式 · `next` | 是 | 用户输入去掉首尾空白后的内容 |
  | `api_call` · `success` | 是（如果设置了 `save_as`） | mock 的响应体 |
  | `api_call` · `failure` | **否** | — |

- **规则：使用变量时，从起点出发、能走到这个节点的每一条路径上，这个变量都必须已经写入。** 只要有一条路径上没有写入，就判为 error。
  - 例子：买家和卖家两条分支最后汇合，但只有卖家分支问过并保存了 `email`。那么汇合之后的 `已发送到 {{email}}` 判为 error，因为走买家分支到这里时没有这个值。
  - 实现方法（必定赋值分析）：起点处已定义的变量只有 `contact.*`。每个节点**进入时**已定义的变量，等于**所有入边**带来的已定义变量的**交集**；每条边带来的变量，等于出发节点进入时已定义的变量，再加上这个出口写入的变量。按这个规则反复迭代到结果不再变化，循环也能处理。从起点走不到的节点不参与计算，它们已经有单独的 warning。

### 3.5 工作区信息（mock）

```jsonc
{ "teams": ["Sales", "Support", "Billing"] }
```

Agent 只能使用这里列出的团队名；如果用户提到不存在的团队，Agent 要向用户追问。

### 3.6 会话

```
Session { id, messages[], versions[Flow], current_version }
```

- 刚创建的会话没有 flow：`versions` 为空，`current_version` 为 `null`。
- **只有本轮最终的差异不为空时**，才追加一个新版本；差异为空就沿用原版本。
- 版本号只在提交时增加，每一轮最多提交一次。

### 3.7 选项匹配规则

校验器和执行引擎共用 `domain` 里的同一个 `normalize()` 函数：去掉首尾空白、把连续空白合并成一个空格、统一转成小写（casefold）。

- **去重**：归一化之后相同的选项算重复，比如 `Buyer` 和 ` buyer` 是重复的，判为 error。
- **匹配顺序**：
  1. 先拿归一化后的输入和归一化后的选项**文字**比较；
  2. 文字匹配不上，并且输入是 `1`–`n` 之间的整数时，再按**序号**匹配；
  3. 都匹配不上就走 `fallback`。
- **数字选项和序号冲突时文字优先**：比如选项是 `["2", "1"]`，用户输入 `1`，命中的是文字为 `"1"` 的那个选项（第 2 项）。
- **写入的值**：不管是按文字还是按序号命中的，`save_as` 都保存选项**原文**。用户输入 `1` 或 `buyer`，保存的都是 `"Buyer"`，这样后面的 `condition equals Buyer` 结果一致。
- `handle` 也使用选项原文：`option:Buyer`。

## 4. 编辑操作（`apply_edits`）

| op | 参数 | 说明 |
|---|---|---|
| `add_node` | `node` | id 由 LLM 命名（如 `ask_role`），系统检查是否重复 |
| `update_node` | `id`, `changes` | 只传要改的字段，系统合并到原节点上；不能改 `type` |
| `remove_node` | `id` | 连带删除与它相关的边 |
| `connect` | `from`, `handle`, `to` | 如果这个出口已经有边，就替换掉 |
| `disconnect` | `from`, `handle` | |
| `set_trigger` | `trigger` | |
| `set_start` | `node_id` | |

一批操作在草稿副本上**原子执行**：只要有一个操作不合法，整批都不生效，并按操作序号返回错误。

### 差异（diff）

差异由 `diff.py` 比较前后两个版本得出，和用了哪些操作无关，所以 `replace_flow` 也能得到准确的差异。

```jsonc
{ "kind": "added | removed | changed",
  "target": "node | edge | trigger | start",
  "id": "节点 id；如果是边，就是 from:handle",
  "before": "...", "after": "..." }   // changed 时提供
```

- 只改了起点：输出一条 `{ "kind": "changed", "target": "start", "before": "ask_role", "after": "greet" }`。
- 改了触发方式：输出 `target: "trigger"`，并带上前后两个值。

## 5. 校验规则

有 **error** 时不能保存为有效版本，也不能开始模拟；**warning** 只做提示，不阻止任何操作。

| 级别 | 规则 |
|---|---|
| error | 数据结构不符合 schema（Pydantic 校验失败） |
| error | 起始节点不存在；边指向不存在的节点；节点 id 重复 |
| error | `handle` 不是该节点类型允许的出口；同一个出口连了多条边 |
| error | 必须连接的出口没有连上（每个选项和 `fallback`、`true`/`false`、`success`/`failure`、`wait` 和 text 模式提问的 `next`） |
| error | 存在一个循环，循环里没有任何 `ask_question` 节点（会无限执行下去，不等用户输入） |
| error | 选项数量不在 1–3 之间，或有重复的选项（按 3.7 节归一化后比较） |
| error | `wait` 的时长超出范围 |
| error | `assign_team` 的团队不在工作区列表里 |
| error | 使用变量时，存在一条能到达这里、但没有写入该变量的路径（规则见 3.4） |
| warning | 存在从起始节点走不到的节点 |
| warning | `api_call` 的 URL 不是 https |
| warning | 提问内容疑似在收集敏感信息（关键词匹配：password、card number、CVV、OTP 等） |

每一条问题的结构是 `{ level, code, message, node_id? }`，前端根据 `node_id` 在图上标出对应的节点。

## 6. 模拟执行引擎

- 状态：`{ current_node_id, variables, transcript[], trace[], status }`
- `status` 的取值：`waiting_input`（等待用户输入）、`completed`（正常结束）、`assigned`（已转人工）、`error`、`max_steps`（超出步数上限）
- 推进规则：从当前节点开始一直往下走，遇到需要用户输入的节点就停下。
  - `ask_question`（buttons 模式）：按 3.7 节的规则匹配。命中时把选项原文写入 `save_as`，走对应选项的出口；匹配不上就走 `fallback`，不写入变量。
  - `ask_question`（text 模式）：把去掉首尾空白的输入写入变量，然后走 `next`。
  - `api_call`：不发出真实请求。按调用方传入的设置返回成功或失败，默认成功。成功时写入 mock 的响应体，失败时不写入。
  - 变量写入的时机和 3.4 节的表格完全一致，所以校验通过的 flow 在运行时不会读到缺失的变量。为了防御，如果运行时真的读到了缺失变量，引擎把状态设为 `error`，而不是替换成空字符串。
  - `wait`：不真的等待，只在轨迹里记一笔"等待了 X 秒"，然后继续。
  - `assign_team`：记录转给了哪个团队；如果没有后续节点，状态变为 `assigned`。
  - 消息中的 `{{变量}}` 会被替换成实际的值。
- 步数上限由 `SIM_MAX_STEPS` 配置。
- **只有一份执行逻辑**：Agent 的 `simulate` 工具和用户在前端用的模拟器，调用的是同一个引擎。

## 7. Agent

### 7.1 每一轮的处理流程

```
用户消息
  → 读取会话：当前 flow、最近几轮对话、工作区信息
  → 有上限的工具调用循环（最多 AGENT_MAX_ITERATIONS 步，tool_choice=required）
       所有修改都写在草稿上；每次修改后系统自动校验，结果随工具返回值交给 Agent
       必须以 finish 或 ask_user 结束
  → 按 7.3 节确定本轮的 status；如果需要提交，就保存为新版本（每轮最多提交一次）
  → 【提交之后】有新版本时，再做一次 LLM 调用生成大白话解释（尽力而为）
       失败或超时：用差异和 finish.summary 拼出一段简短说明代替，
       status 保持 completed / partial 不变，并标记 explanation_source = "fallback"
  → 返回结果
```

**提交是分界线**：提交之前出的任何问题，结果都是 `failed`，flow 不变；提交之后出的问题（目前只有生成解释这一步），不影响 status 和版本。这样用户看到 `failed` 重试时，不会把同一个修改执行两次。

### 7.2 工具

| 工具 | 是否结束本轮 | 作用 | 返回 |
|---|---|---|---|
| `replace_flow(flow)` | 否 | 整体写入 flow；用在从零生成，或用户明确要求重做时 | flow 大纲 + 校验结果 |
| `apply_edits(ops[])` | 否 | 增量修改；flow 已经存在时默认用这个 | flow 大纲 + 校验结果 + 本次差异 |
| `simulate(scenarios[])` | 否 | 每个场景是一组用户输入，可以指定 API 调用的 mock 结果；用来让 Agent 用用户的原意检验自己的结果 | 每个场景的执行路径和最终状态 |
| `ask_user(questions[])` | 是 | 缺少无法合理假设的关键信息时追问，最多 3 个问题 | — |
| `finish(summary, assumptions[], unsupported[])` | 是 | 提交结果；如果有 error，系统会打回，Agent 要接着修 | — |

**为什么是这几个工具：**
- **两个写入工具**：只用 `replace_flow` 的话，每次修改都要重写整份 flow，容易改到用户没要求改的地方；只用 `apply_edits` 的话，第一次生成要用几十个操作拼出整个图，啰嗦又容易出错。
- **写入后直接返回校验结果**：省掉"修改 → 查询 → 校验"这样的多次往返，而且 Agent 不可能跳过校验。
- **`simulate` 的必要性**：校验器只能检查结构，检查不了行为。比如买家和卖家的分支接反了，结构完全合法，只有实际跑一遍才能发现。
- **不提供 `validate` 工具**：校验由系统强制执行，不需要 Agent 主动调用。

### 7.3 每一轮的结果类型

按下表**从上到下**判定，命中第一条就停。"差异"指草稿和当前有效版本的比较结果；`finish` 被调用时，草稿一定已经通过了最终校验，否则会被打回。

| # | 条件 | status | 版本 | 返回的 `flow` / `version` |
|---|---|---|---|---|
| 1 | Agent 循环中 LLM 调用失败（重试之后） | `failed` | 不变 | 原版本；如果还没有 flow，就是 `null` |
| 2 | 步数用完，**没有**调用过结束工具（不管草稿是否合法） | `failed` | 不变 | 原版本；草稿和问题列表放在 `draft` / `issues` 里 |
| 3 | `ask_user` | `clarification` | 不变 | 原版本；**第一轮就追问时是 `null`** |
| 4 | `finish`，差异不为空，`unsupported` 为空 | `completed` | +1 | 新版本 |
| 5 | `finish`，差异不为空，`unsupported` 不为空 | `partial` | +1 | 新版本 |
| 6 | `finish`，差异为空，`unsupported` 不为空 | `declined` | 不变 | 原版本（可能是 `null`） |
| 7 | `finish`，差异为空，`unsupported` 为空（比如"这个流程是做什么的？"，或者要求已经满足） | `completed` | **不变** | 原版本，`diff: []` |

> 说明：
> - 第 2 条：草稿合法但 Agent 没有调用 `finish`，也不自动提交。因为 Agent 没有确认结果符合用户的意图，而且也拿不到 `unsupported`。
> - 被打回的 `finish` 也计入步数。

**三条原则：**
1. 不偷偷删掉需求，也不假装做到了。做不到的需求必须写进 `unsupported`。
2. Agent 不能绕过校验器报的 error：要么改好，要么承认做不到。
3. 上一个有效版本永远不会被破坏。

### 7.4 系统提示词

结构依次是：
1. **角色和原则**：只能使用下面列出的节点；不允许编造团队名或链接；替代方案如果会改变用户意图，就要先追问。
2. **节点目录**：**从 `registry.py` 自动生成**，不手写，保证提示词和代码一致。
3. **编辑操作目录。**
4. **工作区信息。**
5. **一个示例**：题目里的买家/卖家例子，从输入到最终 flow。
6. **结束规则**：每一轮必须以 `finish` 或 `ask_user` 结束。

之后是动态内容：当前 flow 的大纲、最近几轮对话、用户这次的消息。

工具参数在发给服务商时只声明成普通对象（不依赖严格的 JSON schema 模式），真正的校验在后端用 Pydantic 完成。参数不合法时，把错误作为工具结果返回给 Agent，不中断循环。

### 7.5 失败处理与可观测性

- LLM 调用超时或被限流：SDK 自动重试 `LLM_MAX_RETRIES` 次。
  - 发生在 Agent 循环中（提交之前）：返回 `failed`，flow 不变。
  - 发生在生成解释时（提交之后）：使用代替说明，status 和版本都不受影响（见 7.1）。
- 调用了不存在的工具，或者参数不合法：作为错误返回给 Agent，计入步数。
- 每一轮分配一个 `trace_id`，记录每一步的工具名、参数摘要、校验结果、耗时。这些信息写进结构化日志，也随响应返回，前端可以折叠展示。

## 8. API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/sessions` | 创建会话 |
| GET | `/api/sessions/{id}` | 获取会话：对话记录和当前 flow |
| POST | `/api/sessions/{id}/messages` | 发送一条消息，执行一轮 Agent |
| POST | `/api/sessions/{id}/simulations` | 基于当前 flow 开始一次模拟，可以传入 API mock 设置 |
| POST | `/api/simulations/{sim_id}/input` | 在模拟中发送用户输入 |

`POST /messages` 的响应：

```jsonc
{
  "status": "completed | partial | declined | clarification | failed",
  "reply": "给用户看的回复：总结、追问或拒绝原因",
  "flow": { /* 当前有效 flow */ },   // 还没有任何有效版本时为 null（比如第一轮就追问）
  "version": 3,                       // 同上，可以是 null
  "diff": [ /* 见第 4 节差异的定义；没有新版本时为 [] */ ],
  "issues": [ /* 校验问题 */ ],
  "explanation": "大白话解释",        // 没有新版本时为 null
  "explanation_source": "llm | fallback",
  "assumptions": [],
  "unsupported": [],
  "draft": null,      // 只在 failed 时返回
  "trace": [ /* Agent 每一步的记录 */ ]
}
```

## 9. 前端

单页三栏：
- **左栏**：和 Copilot 的对话，展示回复、追问、假设、未实现的需求。
- **中栏**：React Flow 节点图，用 dagre 自动布局；有问题的节点标红或标黄，模拟时高亮当前节点。
- **右栏**：三个 Tab，分别是**解释与问题**、**模拟器**、**JSON**。

## 10. 测试

| 层 | 测什么 |
|---|---|
| `domain` 单元测试 | 每条校验规则都有一个正例和一个反例；编辑操作的原子性；引擎的各种状态（fallback 分支、API 失败分支、步数上限、变量替换） |
| 变量分析 | 分支汇合时只有一条分支写入了变量 → error；两条分支都写入了 → 通过；从 `fallback` 或 `failure` 出口过来后使用该变量 → error；带循环的 flow 能正确收敛 |
| 选项匹配 | `Buyer` 和 `buyer` 判为重复；输入 `1` 和 `buyer` 保存的值相同，都是 `"Buyer"`；数字选项和序号冲突时文字优先 |
| 差异 | 只执行 `set_start` → 版本加 1，差异里只有一条 `target: start`，带前后两个值 |
| Agent 循环 | 用一个按脚本返回结果的假 LLM，覆盖 7.3 节表格里的每一行（包括：第一轮就追问时 `flow` 为 `null`；步数用完、草稿合法但没有调用 `finish`；`finish` 但没有修改）；被校验打回后修好 |
| 提交与解释 | 假 LLM 在 Agent 循环中成功，但生成解释时超时 → status 是 `completed`，版本**只增加一次**，`explanation_source` 是 `fallback` |
| 演示用例 | 3 条演示用的自然语言输入，用真实 LLM 手动跑一遍 |

## 11. 演示用例

1. **题目原例**：买家/卖家分流。演示：Agent 追问帮助文章的链接 → 生成 flow → 解释 → 模拟买家、卖家和乱输入三条路径。
2. **多轮修改**："卖家收到文章后等 1 小时再问一句有没有帮到他" → 演示 `apply_edits` 和差异展示。
3. **有问题的请求**："把 VIP 用户转给 VIP 团队，同时给他们发封邮件" → 演示团队不存在时的追问，以及邮件被标为 `unsupported`。
