# MiniAgent

**开发语言：Python 3.8+**

> 一个会自己写代码、自己进化的桌面 AI Agent。

中文原生支持，无需看懂英文，打开就能用。

---

## ✨ 它和普通 AI 助手有什么不同？

| 特性 | MiniAgent | 普通 AI 助手 |
|------|-----------|--------------|
| 🤖 **双模型协作** | 主 AI 负责思考决策和调用脚本及协作AI，协作 AI 负责记忆 | 单一模型，所有工作自己扛 |
| 🔧 **自己写扩展** | AI 可以自己生成扩展代码并调用，能力自我进化 | 功能固定，无法自行扩展 |
| 💓 **心跳机制** | 空闲时自主行动，可以学习、整理记忆、执行定时任务 | 只能被动响应用户输入 |
| 👀 **双屏透明化** | 左边看对话结果，右边看 AI 的思考过程 | 黑盒运行，看不到中间过程 |
| 📋 **任务管理** | 支持定时任务、循环任务、代码任务，无人值守自动执行 | 无任务调度能力 |

**一句话总结：你给它一个目标，它自己想办法搞定。**

---

## 🚀 快速上手

### 1. 下载源码

**GitHub（国际）：**
```bash
git clone https://github.com/juno20252026/miniagent.git
cd miniagent
```

**Gitee（国内镜像，速度更快）：**
```bash
git clone https://gitee.com/juno20252026/miniagent.git
cd miniagent
```

> 任选一个地址克隆即可，内容完全相同。

### 2. 安装依赖

**基础依赖（必须安装）：**
```bash
pip install -r requirements.txt
```

**如果安装速度慢，可使用国内镜像：**
```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

**依赖清单：**

| 依赖包 | 用途 |
|--------|------|
| `requests` | AI 客户端 API 调用 |
| `numpy` | 向量计算（语义检索） |
| `sentence-transformers` | 语义检索模型（向量嵌入） |
| `jieba` | 中文分词 |
| `psutil` | 看门狗进程监控（暂未使用） |
| `tkinter` | 桌面 GUI 界面（Python 内置，无需额外安装） |
| `sqlite3` | 本地数据库（Python 内置，无需额外安装） |

> 以上依赖已在 `requirements.txt` 中列明，执行上述命令会自动全部安装。

### 3. 启动
```bash
python miniagent.py
```

首次启动会自动弹出配置窗口，选择 AI 后端，填好 API Key 即可。

---

## 🧠 支持的 AI 后端

| 后端 | 说明 | API Key |
|------|------|---------|
| **Ollama** | 本地运行，免费 | 无需 |
| **通义千问** | 阿里云 DashScope | 需要 |
| **DeepSeek** | 国内 API，性价比高 | 需要 |
| **智谱 GLM** | 免费模型 `glm-4.5-flash` | 需要 |
| **TokenHub** | 支持任意 OpenAI 兼容接口 | 需要 |

---

## 📁 项目结构

```
miniagent/
├── miniagent.py           # 主入口
├── ai_client.py           # AI 客户端（多模型支持）
├── config_manager.py      # 配置管理（首次启动自动弹窗）
├── extension_manager.py   # 扩展系统（AI 自己写扩展）
├── mission_manager.py     # 任务管理
├── knowledge_base.py      # 知识库管理
├── semantic_retriever.py  # 语义检索
├── prompts.py             # 系统提示词
├── json_parser.py         # JSON 解析器
├── simple_logger.py       # 日志模块
├── watchdog.py            # 看门狗
├── requirements.txt       # 依赖清单
└── README.md              # 本文件
```

---

## 🎯 使用场景

- **个人助理**：定时提醒、每日报告、信息采集
- **知识管理**：构建个人知识库，语义检索
- **自动化运维**：定时执行脚本，无人值守
- **学习伴侣**：与 AI 协作学习新知识

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

如果你有好的想法或发现 Bug，请随时提出。

---

## 📄 许可证

**MIT License** © 2026 马军

随意使用、修改、分发。

---

## ⭐ 支持

如果这个项目对你有帮助，点个 Star 就是对我最大的鼓励！

---

**Made with love by a developer who believes AI should be able to grow itself.**