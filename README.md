# 🗞️ RMRB-Canary

<p align="center">
  <strong>政策信号早期预警系统</strong><br/>
  比市场早一步读懂官方叙事
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/LLM_calls-zero-orange?style=flat-square" alt="Zero LLM">
  <img src="https://img.shields.io/badge/Claude_Code-ready-purple?style=flat-square" alt="Claude Code">
</p>

---

> **当一个行业被点名，往往已经太晚了。**
>
> RMRB-Canary 在政策信号进入公众视野之前捕捉它——通过对官方媒体叙事的系统性解读，将预警时间从「运动期」前移到「铺垫期」。

---

## 它解决什么问题

大多数企业在看到媒体密集报道时才意识到监管压力——那时政策已进入执行阶段，调整窗口所剩无几。

RMRB-Canary 的目标是**把预警时间前移**：在关键词出现频次还不高、但信号结构已经在变化时，给出可操作的风险判断。

---

## 核心能力

**叙事框架识别**
同一议题在不同官方叙事框架下含义截然不同。RMRB-Canary 自动判断目标议题的叙事归属，这是后续所有信号解读的基准。

**话语强度分级**
官方表述有温度之分，从「研究探索」到「专项打击」之间存在可量化的梯度。系统实时追踪强度变化，检测非线性突变。

**部委协同检测**
跨部委联合行动是政策进入执行倒计时的强预测变量。系统自动识别涉及部委数量与协同模式。

**政策时钟校正**
相同强度的信号在一年中的不同节律阶段有不同的行动时差。系统内置年度周期校正，避免误判窗口。

**沉默信号检测**
议题从高频报道中突然消失，有时是比点名更危险的信号。系统追踪报道缺失并告警。

**多期趋势对比**
7 / 30 / 90 日滚动趋势线，叙事框架变化次数，让你看到方向而不只是快照。

**多源交叉验证**
官方媒体联动 + 社交平台热度，官民叙事张力越大，政策落地阻力越高。

---

## 快速开始

**安装依赖**

```bash
pip install requests beautifulsoup4
```

**克隆仓库**

```bash
git clone https://github.com/Eleven1111/rmrb-canary.git
cd rmrb-canary
```

**运行分析**

```bash
python3 -m agent.agent --keyword 光伏 新能源 储能
```

---

## 使用示例

```bash
# 分析多个关键词
python3 -m agent.agent --keyword 光伏 新能源 储能

# 指定历史日期
python3 -m agent.agent --keyword 教育 培训 --date 20260405

# 跳过多源交叉验证（更快）
python3 -m agent.agent --keyword 光伏 --skip-media

# 精简输出（适合管道处理）
python3 -m agent.agent --keyword 光伏 --compact

# 查看历史记录
python3 -m agent.agent --history
```

JSON 输出到 `stdout`，进度日志输出到 `stderr`：

```bash
python3 -m agent.agent --keyword 光伏 --compact --skip-media 2>/dev/null \
  | jq '.summary_line'
```

---

## 与 Claude Code 协作

RMRB-Canary 只做计算，不做推理。它输出结构化 JSON，由 Claude Code 完成语义解读、报告撰写和战略建议。两者职责清晰，互不越界。

详细分析框架和使用指南请参见 [SKILL.md](./SKILL.md)（需配合 Claude Code 使用）。

---

## 目录结构

```
rmrb-canary/
├── agent/
│   ├── agent.py          # 主管道
│   ├── tools/            # 各分析模块
│   └── store/            # 历史数据存储
├── scripts/              # 独立爬虫脚本
├── SKILL.md              # Claude Code 分析框架
└── README.md
```

历史分析保存至 `~/.rmrb_canary/history.db`（SQLite），跨会话持久化。

---

## License

MIT
