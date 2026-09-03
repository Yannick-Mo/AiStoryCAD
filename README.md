# AiStoryCAD

**把你的创作灵感交给一个 AI 助手，它懂故事。**

AiStoryCAD 是一个专为小说作者打造的 AI 辅助创作平台。它不是简单的文本生成器，而是一个**深度理解叙事结构**的创作伙伴——从你脑海中的模糊想法，到完整的故事大纲、章节、场景、角色关系，它全程参与。

---
## 界面截图

<div align="center">
  <img src="img-ui/网站首页.png" alt="网站首页" width="45%" />
  <img src="img-ui/幕.png" alt="幕" width="45%" />
</div>
<div align="center">
  <img src="img-ui/章节点.png" alt="章节点" width="45%" />
  <img src="img-ui/ai助手.png" alt="AI 助手" width="45%" />
</div>
<div align="center">
  <img src="img-ui/灵感生成.png" alt="灵感生成" width="45%" />
  <img src="img-ui/灵感生成-故事开头.png" alt="灵感生成-故事开头" width="45%" />
</div>

---

## 为什么需要 AiStoryCAD？

写小说最难的部分往往不是"写"，而是**组织**——几十个章节、上百个场景、错综复杂的人物关系和情节线索。AiStoryCAD 的 AI 助手理解你的整个故事结构，可以：

- **回答"这个角色在第 3 章的行为是否前后矛盾？"** ——它读过你的整个项目
- **建议"第 7 章的节奏偏慢，可以在场景 A 和 B 之间插入一个冲突场景"** ——它分析过你的叙事节奏
- **执行"帮我生成第 12-15 章的详细大纲"** ——它理解你的故事风格和走向
- **搜索"维多利亚时期伦敦的街道布局"** ——它内置隐私优先的联网搜索能力

它不是聊天机器人，是一个**能操作你项目的 AI 协作者**。

---

## 核心能力

### AI 超级助手（SuperAgent）

这是 AiStoryCAD 的核心。不同于简单的 LLM 对话，SuperAgent 是一个**自主代理**——它调用工具来读写你的项目数据、分析章节结构、检查叙事一致性、搜索网络资料，然后基于这些信息做出判断和创作。

它的工作流程：

```
你的提问 → AI 理解意图 → 调用工具读取项目数据
         → 思考分析 → 可能需要更多工具调用
         → 写出回答或修改项目 → 继续对话
```

支持两种模式：

| 模式 | 适用场景 |
|---|---|
| **协作模式** | AI 可以读写你的项目——生成大纲、修改场景、创建角色 |
| **对话模式** | AI 只读不写——提供建议和分析，不修改你的作品 |

### 叙事分析

- **节奏分析** —— 自动检测每个场景的叙事节奏分布（动作、对话、描述、内心活动），用可视化图表告诉你故事哪里拖沓、哪里仓促
- **一致性检查** —— 跨章节检测角色描述、世界观设定、情节逻辑的冲突。比如"第 5 章说角色 A 是蓝眼睛，第 12 章变成了棕色"

### 项目自动生成

输入一段素材（一个核心理念、一段人物简介、甚至几句零散的笔记），AI 自动生成完整的项目结构——幕、章节、场景、角色、关系图谱。不是套模板，而是基于你的素材量体裁衣。

### 灵感引擎

- **故事开头** —— 根据你的类型偏好生成有吸引力的开场
- **写作挑战** —— 在瓶颈期给你新鲜的创作练习

---

## 技术特色

| 特色 | 说明 |
|---|---|
| **自主代理架构** | 基于自主循环（autonomous loop）实现，无需 LangGraph 等外部框架 |
| **RAG 知识检索** | 基于 pgvector 的混合搜索，写作指南和创作技巧可注入上下文 |
| **MCP 协议支持** | 暴露标准 Model Context Protocol SSE 端点，任何 MCP 兼容的 AI 客户端可直接与 AiStoryCAD 交互 |
| **隐私优先搜索** | 通过 SearXNG 自托管元搜索引擎，不向第三方泄露你的搜索数据 |
| **双版本 API** | v1 模式化代理与 v2 流式 SSE + 工具调用共存，平滑演进 |

---

## 技术架构

### 自主代理循环（Autonomous Loop）

SuperAgent 的核心是一个**自主循环架构**，不依赖 LangGraph 或 Semantic Kernel 等外部框架。每一轮对话的运行流程：

```
LLM 流式响应 + 工具调用 → 流式执行器调度工具
                       → 拦截器检查（模式门控/确认）
                       → 结果注入上下文
                       → 判断是否继续（工具链）
                       → 最终生成回复
```

每一轮都是**有状态的**——项目上下文、对话历史、工具结果都保持在 `LoopState` 中传递，支持跨轮次的连续工具调用链（如：查询章节列表 → 读取特定场景 → 分析节奏 → 给出建议）。

### 流式工具执行器（StreamingToolExecutor）

执行器采用**三优先级并发模型**，在 LLM 流式响应的同时就开始执行工具，不等待完整响应：

| 优先级 | 类型 | 行为 |
|---|---|---|
| 🔵 SAFE | 只读工具 | 立即并发执行（通过 asyncio.Lock 序列化 DB 访问），结果边执行边推送给前端 |
| 🟡 EXCLUSIVE | 写入工具 | 等待所有 SAFE 工具完成，串行执行，避免写冲突 |
| 🔴 BARRIER | 屏障工具 | 等待 SAFE + EXCLUSIVE 全部完成后再执行，保证看到最终状态 |

这种设计让**读操作零延迟**——AI 还在继续输出 token 时，读取数据的工具已经在跑了。

### 上下文压缩（Context Compression）

大模型上下文窗口有限，长对话会触及 token 上限。AiStoryCAD 实现了**分层压缩策略**：

- **主动压缩** — 每轮对话后检测消息数量，超过阈值时自动压缩：保留系统提示和最近对话，对中间轮次进行摘要化压缩
- **被动压缩** — 当 LLM 返回上下文超长错误（413 / token limit）时，触发紧急压缩，丢弃历史轮次中的工具调用细节，只保留最终结果摘要
- **扫描计数** — 记录每轮的消息压缩扫描状态，避免重复压缩

压缩后 token 数通常减少 60-70%，且不会丢失项目上下文信息。

### 拦截器层（Interceptor Layer）

所有工具调用在正式执行前经过拦截器检查：

- **模式门控** — 协作模式下允许读写，对话模式下自动拦截写入工具并给出提示
- **写入失效与上下文刷新** — 当写入工具成功修改项目数据后，自动将关联的只读工具标记为失效，下次调用时重新加载最新数据
- **参数级验证** — 检测连续参数缺失错误（如 DeepSeek 模型常见的不提供必填参数），累计 3 次后注入系统提示打断重试循环

### 错误恢复系统（Recovery System）

当 LLM 流式调用失败时，自动进入恢复流程：

```
错误分类 → 重试 → 带错误上下文重试 → 压缩上下文重试 → 切换模型 → 放弃
```

每个步骤都有延时机制和状态跟踪，避免死循环。

### RAG 混合检索

知识检索层结合两种搜索策略：

- **向量检索** — 使用 pgvector 对用户项目文档和写作指南进行语义搜索（支持配置 embedding 模型）
- **全文检索** — PostgreSQL 原生全文搜索作为后备，无 embedding 模型时自动降级

检索结果被组织成结构化的上下文块，注入 LLM 的 system prompt 中。

### MCP 协议支持

实现了 [Model Context Protocol](https://modelcontextprotocol.io) 标准 SSE 端点，将 AiStoryCAD 的写作工具集暴露为 MCP 工具。任何兼容 MCP 的 AI 客户端（如 Claude Desktop）可以直接连接到 AiStoryCAD，对用户的项目进行读写操作。

---



## 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.11 · FastAPI · Uvicorn · SQLAlchemy 2.0 (async) |
| **前端** | React 18 · TypeScript · Vite · Tailwind CSS · React Flow |
| **数据库** | PostgreSQL 15 + pgvector |
| **缓存** | Redis 7 |
| **搜索** | SearXNG（自托管元搜索引擎） |
| **LLM** | OpenAI 兼容 API（默认 DeepSeek） |
| **容器化** | Docker Compose（5 个服务） |

---

## 快速开始

### 前置要求

- Docker & Docker Compose v2
- 一个 OpenAI 兼容的 LLM API Key（DeepSeek / OpenAI / 其他）

### 启动（生产/日常使用）

```bash
# 1. 配置环境变量（LLM 的 base-url / api-key 也可以直接在网页的
#    "模型配置" 里设置并热生效，环境变量仅作启动兜底）
cp .env.example .env
# 编辑 .env，至少设置 LLM_API_KEY、SEARXNG_SECRET_KEY

# 2. 构建并启动全部 5 个服务（db / redis / backend / searxng / frontend）
docker compose up -d --build

# 3. 访问
#   前端:      http://localhost:5173   （nginx 托管静态产物，/api 与 /mcp 反代到后端）
#   API:       http://localhost:8000
#   Swagger:   http://localhost:8000/docs
```

单用户本地工具：无需注册/登录，首次打开首页若检测到未配置模型会自动弹出"模型配置"窗口。

生产配置**不挂载源码**：backend 镜像只含运行时依赖，frontend 由 nginx 托管构建产物。

### 本地开发（源码热重载）

```bash
# 叠加 docker-compose.dev.yml：backend 挂载源码 + uvicorn --reload，
# frontend 跑 vite dev server（对外端口 8080，代理 /api、/mcp 到 backend）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# 访问 http://localhost:8080 （vite dev server）
```

在容器里跑后端测试：

```bash
docker compose exec -T backend pytest tests -q
```

不使用 Docker 的本机开发：

```bash
# 后端
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # 含测试依赖
# 确保 PostgreSQL + pgvector + Redis 已运行
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev
```

---

## 配置

核心环境变量（`.env`）：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | LLM API 密钥（启动兜底；可在前端"模型配置"中修改） | — |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | 模型名称 | `deepseek-v4-flash` |
| `SEARXNG_SECRET_KEY` | SearXNG 会话签名密钥（生产必填随机值） | — |
| `DATABASE_URL` | PostgreSQL 连接串 | `postgresql+asyncpg://postgres:postgres@db:5432/storyforge` |
| `REDIS_URL` | Redis 连接串 | `redis://redis:6379/0` |

运行时模型配置（主模型 / 中间压缩模型 / 备用模型 / Embedding）保存在数据库 `model_config` 表，
可通过前端右上角齿轮按钮修改，保存后立即生效（无需重启）。

完整配置项见 `backend/app/config.py`。后端依赖锁定在 `backend/requirements.lock`（主要依赖精确版本），可用 `pip install -r requirements.lock` 复现生产依赖；测试依赖见 `backend/requirements-dev.txt`（仅 CI 与本地开发安装，不进生产镜像）。CI 配置见 `.github/workflows/ci.yml`（后端 pytest + 前端 typecheck/build）。

---

## 项目结构

```
AiStoryCAD/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/                 # REST 路由
│   │   ├── agent/               # AI 代理系统（核心）
│   │   │   ├── super_agent.py   # v2 SuperAgent
│   │   │   ├── tools/           # 工具注册中心
│   │   │   ├── memory/          # 对话记忆
│   │   │   └── prompts/         # LLM 提示词模板
│   │   ├── llm/                 # LLM 客户端
│   │   ├── knowledge/           # RAG 引擎
│   │   ├── storycad/            # 叙事数据模型
│   │   ├── project/             # 项目 CRUD
│   │   ├── mcp/                 # MCP 协议服务器
│   │   └── user/                # 用户认证
│   ├── alembic/                 # 数据库迁移
│   ├── requirements.txt         # 生产依赖
│   ├── requirements-dev.txt     # 测试依赖（pytest 等）
│   ├── requirements.lock        # 主要依赖精确版本锁定
│   └── tests/                   # 后端测试
├── frontend/
│   ├── src/
│   │   ├── pages/               # 页面（主页/编辑器/登录）
│   │   ├── api/                 # API 客户端
│   │   ├── context/             # React 上下文
│   │   └── hooks/               # 自定义 Hooks
│   ├── nginx.conf               # 生产 nginx 配置（托管静态产物 + 反代 /api、/mcp）
│   └── package.json
├── docker-compose.yml           # 生产编排（5 服务）
├── docker-compose.dev.yml       # 本地开发覆盖（源码挂载 + 热重载）
├── .env.example                 # 环境变量模板
├── searxng/                     # SearXNG 配置
└── .github/workflows/ci.yml     # CI：后端 pytest + 前端 typecheck/build
```

---

## License

MIT
