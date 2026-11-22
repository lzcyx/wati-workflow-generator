# Wati Chatbot node reference

This reference brings together the take-home exercise requirements and Wati's official node descriptions. It provides the terminology for the project's Flow data model. Section 8 lists the details that the design document needs to define.

## Sources

| ID | Source | Coverage |
|---|---|---|
| S1 | *Automation Builder Copilot – Take-home exercise* (PDF) | Required node vocabulary and output requirements |
| S2 | [Understanding nodes in Chatbot Builder](https://support.wati.io/en/articles/11463029-understanding-nodes-in-chatbot-builder), linked as "Wati chatbot node reference" in the exercise | Node concepts, triggers, connections, categories, and AI nodes |

---

## 1. Node requirements (S1)

> Use Wati's chatbot node vocabulary as your model: send message, ask question, condition, API call, assign to team, wait, and similar nodes. The output should feel recognizable to a Wati user.

- Use Wati's terminology and cover Send Message, Ask Question, Condition, API Call, Assign to Team, and Wait.
- The exercise also allows similar nodes.
- The output should be recognizable to Wati users.
- Intent parsing should capture the **trigger, conditions, actions, and timing**.

## 2. Basic concepts (S2)

| Concept | Description |
|---|---|
| Node | A single step in a chatbot workflow. A node can send a reply, ask a question, trigger an action, route a conversation, or define conversation logic. |
| Chatbot | A set of connected nodes that guide the conversation and determine how the bot responds. |
| Trigger | A **keyword or action** that starts the chatbot. |
| Starting node | The first node to run after the trigger fires. The flow then proceeds according to the user's answers. |
| Arrows | Connections between nodes that determine the conversation direction, the next step, and the conditions for moving between nodes. |
| Branching | Additional nodes can create separate paths for different replies or conditions. In Wati's example, **Yes** leads to the next node and **No** ends the conversation. |

These concepts inform the structure of a flow.

1. A flow has **one trigger** and **one starting node**.
2. A flow is a **directed graph**. Nodes are steps, and edges represent transitions that can depend on conditions such as the user's reply.
3. A flow can **end** at a node.

## 3. Six required nodes

| Exercise term (S1) | Purpose | Outputs or branches | Basis |
|---|---|---|---|
| **Send Message** | Send a message to the user | One output leading to the next node | S1 and S2's description of nodes that send replies |
| **Ask Question** | Ask the user a question and wait for a reply | Branch by reply, such as separate Yes and No paths | S1 and S2's description of questions and its Yes/No example |
| **Condition** | Choose a path based on a condition | Multiple branches based on whether a condition is met | S1 and S2's descriptions of conversation logic and conditional branching |
| **API Call** | Call an external system | Unspecified | S1 and S2's descriptions of actions and the Integrations category |
| **Assign to Team** | Route a conversation to a specific team, as in the exercise example "route buyers to sales" | Unspecified | S1 and S2's description of conversation routing |
| **Wait** | Pause the flow for a specified time to implement the timing requirement | Continue to the next node after the delay | S1 and the *Time Delay Node* article related to S2 |

## 4. Node categories and plans (S2)

| Plan | Available nodes |
|---|---|
| Growth | Basic messaging, questions, and conditions for simple flows |
| Pro / Business | Basic nodes plus **Operations**, **Integrations**, and **Catalog** |

Based on the node names, Send Message, Ask Question, and Condition map to basic features. Assign to Team maps to Operations, and API Call maps to Integrations.

## 5. AI nodes (S2, outside project scope)

| Node | Purpose | Outputs | Can start a flow |
|---|---|---|---|
| Wati AI Assist | Collect and save one contact attribute through conversation | Success and Failure paths | Yes |
| Astra AI Agent | Hand the conversation off to Astra AI Agent | Ends the chatbot flow | No |
| Custom AI Agent | Hand the conversation off to the user's integrated AI agent | Ends the chatbot flow | No |

Supported channels include WhatsApp, Instagram, and Facebook Messenger.

## 6. Other nodes for future consideration

S2 mentions the following nodes or categories in related articles. They are outside the current project scope.

| Node or category | Description in S2 |
|---|---|
| Template | Covered in the separate *Template Node* article |
| Trigger Chatbot | Lets one chatbot start another chatbot |
| Smart Escalation | Assigns a conversation to a team when a customer asks for a human or shows frustration |
| Catalog nodes | Listed as a category |

## 7. Exercise example

> When a new contact messages us, ask if they're a buyer or seller, then route buyers to sales and send sellers our help article.

| Intent element | Requirement | Node or flow element |
|---|---|---|
| Trigger | A new contact sends a message | Flow trigger |
| Action | Ask whether the contact is a buyer or seller | Ask Question with Buyer and Seller options |
| Condition | Branch according to the answer | Branches on Ask Question or a subsequent Condition |
| Action (Buyer) | Route the contact to the sales team | Assign to Team (Sales) |
| Action (Seller) | Send the help article | Send Message with the article link |
| Timing | Unspecified | — |

The example leaves three questions for clarification or validation.

- What happens if the user answers with something other than Buyer or Seller?
- What is the help article URL?
- What is the exact name of the sales team in the workspace?

## 8. Open design decisions

The [design document](./design.md) resolves the following details left open by S1 and S2.

- **Node fields** include the message format, question mode (options or free text), answer variable, Condition operators, API Call request and response structure, and Wait duration units and limits.
- **Branch rules** include whether Ask Question requires a fallback, how Condition represents its branches, and whether API Call has success and failure paths.
- **Flow termination** can use an explicit End node or rely on the absence of an outgoing edge.
- **Supported triggers** may include new contact messages, keywords, and other trigger types.
