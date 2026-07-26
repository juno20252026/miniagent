# 🧠 MiniAgent

**一个会自己写代码、自己进化、能收邮件的桌面 AI Agent。**

> 中文原生支持，无需看懂英文，打开就能用。

---

## ✨ 它和普通 AI 助手有什么不同？

| 功能 | 说明 |
|------|------|
| 🧠 **双模型协作** | 一个 AI 负责思考决策，另一个 AI 负责执行子任务，左右脑分工 |
| 🔄 **自己写扩展** | AI 发现自己缺功能 → 自己写代码 → 自己调用 → 能力自我进化 |
| 💓 **心跳机制** | 空闲时自己学习、整理记忆、执行定时任务，无需你操作 |
| 🖥️ **双屏透明化** | 左边看对话结果，右边看 AI 的思考过程（搜索/计划/代码执行） |
| 📧 **邮件通道** | 发一封邮件，AI 在云端就能收到并处理，不需要你打开界面 |
| 📋 **任务管理** | 支持定时任务、循环任务、代码任务，无人值守自动执行 |

---

## 🚀 3 分钟快速上手

### 1️⃣ 下载源码

```bash
git clone https://github.com/juno20252026/miniagent.git
cd miniagent
```

### 2️⃣ 安装依赖

```bash
pip install -r requirements.txt
```

### 3️⃣ 启动

```bash
python miniagent.py
```

首次启动会自动弹出配置窗口，选择你用的 AI 后端，填好 API Key 即可。

---

## 📦 支持的 AI 后端

| 后端 | 说明 |
|------|------|
| **Ollama** | 本地运行，免费，无需 API Key |
| **通义千问** | 阿里云 DashScope |
| **DeepSeek** | 国内 API，性价比高 |
| **智谱 GLM** | 免费模型 `glm-4.5-flash` |
| **TokenHub** | 支持任意 OpenAI 兼容接口 |

---

## 🗂️ 项目结构

```
miniagent/
├── miniagent.py           # 主入口
├── ai_client.py           # AI 客户端（多模型支持）
├── config_manager.py      # 配置管理（首次启动自动弹窗）
├── extension_manager.py   # 扩展系统（AI 自己写扩展）
├── mission_manager.py     # 任务管理（定时/循环/代码任务）
├── knowledge_base.py      # 知识库管理
├── semantic_retriever.py  # 语义检索（向量搜索）
├── prompts.py             # 系统提示词
├── json_parser.py         # JSON 解析器
├── simple_logger.py       # 日志模块
├── watchdog.py            # 看门狗（进程守护）
├── requirements.txt       # 依赖清单
└── README.md              # 本文件
```

---

## 🆚 对比 OpenClaw

| 功能 | MiniAgent | OpenClaw |
|------|-----------|----------|
| 双屏思考透明化 | ✅ | ❌ |
| AI 自己写扩展 | ✅ | ❌ |
| 心跳自主进化 | ✅ | ❌ |
| 中文本地化 | ✅ | ❌ |
| 桌面 GUI | ✅ | ❌ |

---


---

## 📄 许可证

MIT License © 2026 [马军] —— 随意使用、修改、分发。

---

## ⭐ 支持

如果这个项目对你有帮助，点个 Star 就是对我最大的鼓励！

---

**Made with ❤️ by a developer who believes AI should be able to grow itself.**
