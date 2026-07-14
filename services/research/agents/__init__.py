"""分析 Agent（spec §11）：证据整理器，不是数值预测器。

* ``agents.repository``：只读产品数据库的 PIT 数据层（Agent 不直接访问外部数据源）。
* ``agents.tools``：**只有 5 个**工具，白名单固定，as_of / symbol 由服务端绑定。
* ``agents.prompts``：系统提示 + 外部文档的不可信内容隔离。
* ``agents.schema``：固定输出 Schema（封闭），校验失败最多重试一次。
* ``agents.client``：OpenAI 兼容 provider；未配置时返回 ``None`` → 调用方降级模板摘要。
* ``agents.runner``：工具循环 + 重试 + 降级。
* ``agents.analyst``：装配成可落库的 ``AnalysisDraft``（先事实、后证据、证据必须过闸）。

**本文件刻意不做聚合再导出**：``anomaly`` 依赖 ``agents.repository``，而 ``agents.analyst``
依赖 ``anomaly``；一旦在包 ``__init__`` 里导入 ``analyst``，先 import ``anomaly`` 的路径就会
撞上循环导入。请一律从具体子模块导入。
"""

from __future__ import annotations
