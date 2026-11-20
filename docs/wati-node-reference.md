# Wati Chatbot 节点参考

> 本文档整理题目要求与 Wati 官方节点说明，作为本项目 Flow 数据模型的词汇依据。
> 只记录来源中明确出现的内容；来源未说明的细节统一放在「待设计决定」，由设计文档决定，不在此臆造。

## 来源

| # | 来源 | 内容 |
|---|---|---|
| S1 | 题目 PDF：*Automation Builder Copilot – Take-home exercise* | 指定节点词汇与输出要求 |
| S2 | Wati 帮助文章 [Understanding nodes in Chatbot Builder](https://support.wati.io/en/articles/11463029-understanding-nodes-in-chatbot-builder)（PDF 中 "Wati chatbot node reference" 链接） | 节点概念、启动、连接、分组、AI 节点 |

---

## 1. 题目对节点的要求（S1）

原文：

> Use Wati's chatbot node vocabulary as your model: send message, ask question, condition, API call, assign to team, wait, and similar nodes. The output should feel recognizable to a Wati user.

要点：

- 节点命名沿用 Wati 词汇，**必须覆盖**：send message、ask question、condition、API call、assign to team、wait。
- 可以加入"similar nodes"，但不是必须。
- 输出要让 Wati 用户一眼认得出来。
- 意图解析需要覆盖四类信息：**trigger、conditions、actions、timing**。

## 2. 基本概念（S2）

| 概念 | 官方说明 |
|---|---|
| Node（节点） | chatbot 工作流中的单个步骤。节点可以发送回复、提问、触发动作、路由会话、定义对话逻辑 |
| Chatbot | 由多个相互连接的节点组成，引导对话并决定机器人的回复 |
| 启动（Trigger） | 由**关键词或某个动作**触发 chatbot |
| Starting node | 触发后从第一个节点（起始节点）开始执行，再根据用户回答沿流程推进 |
| 连接（Arrows） | 节点之间用箭头连接，箭头决定：对话方向、下一步、在节点间流转的条件 |
| 分支 | 可以继续挂接节点，按不同回复或条件分叉。官方示例：用户选 **Yes** 进入下一节点，选 **No** 则结束对话 |

由此得到的结构约束：

1. 一个 flow 有**一个触发条件**和**一个起始节点**。
2. flow 是**有向图**，节点是步骤，边（箭头）表示流转，边上可以带条件（例如对应哪个回复）。
3. 流程可以在某个节点**结束**。

## 3. 核心节点（题目点名的 6 种）

| 题目用词 (S1) | 作用 | 出口 / 分支 | 依据 |
|---|---|---|---|
| **Send Message** | 向用户发送一条消息 | 单一出口，进入下一节点 | S1；S2「节点可以发送回复」 |
| **Ask Question** | 向用户提问并等待回复 | 按用户回复分支（如 Yes / No 各走一条路） | S1；S2「提问」及 Yes/No 示例 |
| **Condition** | 根据条件决定走哪条路 | 多条分支，按条件成立与否流转 | S1；S2「定义对话逻辑、按条件分叉」 |
| **API Call** | 调用外部系统 | 来源未说明 | S1；S2「触发动作」、Integrations 分组 |
| **Assign to Team** | 把会话转给指定团队（如题目示例 "route buyers to sales"） | 来源未说明 | S1；S2「路由会话」 |
| **Wait** | 在流程中等待一段时间，对应意图中的 timing | 等待结束后进入下一节点 | S1；S2 相关文章 *Time Delay Node* |

## 4. 节点分组与套餐（S2）

| 套餐 | 可用节点 |
|---|---|
| Growth | 基础功能：发送消息、提问、设置条件，适合简单流程 |
| Pro / Business | 在基础功能上增加 **Operations**、**Integrations**、**Catalog** |

与核心节点的对应关系（按名称推断）：Send Message、Ask Question、Condition 属于基础功能；Assign to Team 属于 Operations；API Call 属于 Integrations。

## 5. AI 节点（S2，本项目不实现）

| 节点 | 作用 | 出口 | 能否作为起始节点 |
|---|---|---|---|
| Wati AI Assist | 通过对话收集一个联系人属性并保存 | Success / Failure 两条路径 | 可以 |
| Astra AI Agent | 把会话交给 Astra AI Agent | 在此结束 chatbot | 不可以 |
| Custom AI Agent | 把会话交给用户自己接入的 AI Agent | 在此结束 chatbot | 不可以 |

支持渠道：WhatsApp、Instagram、Facebook Messenger。

## 6. 其他 "similar nodes"（未来可扩展，不展开）

S2 相关文章中提到、但本期不实现的节点：

| 节点 | S2 中的描述 |
|---|---|
| Template | 有专门文章（*Template Node*），未展开 |
| Trigger Chatbot | 让一个 chatbot 启动另一个 chatbot |
| Smart Escalation | 当客户要求人工或表现出不满时，把会话分配给团队 |
| Catalog 类节点 | 仅提到分组名称 |

## 7. 题目示例拆解

输入：

> When a new contact messages us, ask if they're a buyer or seller, then route buyers to sales and send sellers our help article.

| 意图要素 | 内容 | 节点 |
|---|---|---|
| Trigger | 新联系人发来消息 | flow 触发条件 |
| Action | 询问是买家还是卖家 | Ask Question（选项：Buyer / Seller） |
| Condition | 按回答分支 | Ask Question 的分支，或接一个 Condition |
| Action（Buyer） | 转给销售团队 | Assign to Team（Sales） |
| Action（Seller） | 发送帮助文章 | Send Message（帮助文章链接） |
| Timing | 未提及 | — |

示例中没有说明、需要追问用户或由校验器提示的点：

- 用户回答既不是 Buyer 也不是 Seller 时怎么办？
- 帮助文章的链接是什么？
- 销售团队在系统里具体叫什么？

这正是多轮追问（clarify）和校验器要处理的典型情况。

## 8. 待设计决定（来源未说明）

以下内容 S1、S2 都没有给出，由 `docs/design.md` 统一决定：

- 各节点的具体字段：消息内容格式、提问方式（选项还是自由文本）、回答存到哪个变量、Condition 的运算符、API Call 的请求和响应结构、Wait 的时长单位与上限。
- 各节点的出口规则：Ask Question 有没有兜底分支、Condition 的分支形式、API Call 有没有成功和失败两个出口。
- 结束方式：用显式的 End 节点，还是没有出边即结束。
- 支持哪些触发类型：新联系人消息、关键词等。
