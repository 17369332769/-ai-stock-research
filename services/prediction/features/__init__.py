"""Point-in-time 特征层（spec §9.2 / §9.3.1）。

PIT 的三道防线（SQL 过滤 / 可见性过滤 / 构造断言）都在包内闭合，见 ``pit.py`` 与 ``panel.py``。

**这里刻意不做 re-export**：features / training / inference / evaluation / analogs 五个子包
互相引用（例如 inference.loader 要读 training 落的产物，evaluation.settlement 要用
inference 存下来的复权锚点）。如果每个 __init__ 都急切地把子模块全 import 一遍，
包的初始化顺序就会绕成环，`import services.prediction.training.samples` 这种最普通的写法
都会炸在 partially-initialized module 上。具体模块具体 import，环就不存在。
"""

DEFAULT_FEATURE_SET_VERSION = "v1"

__all__ = ["DEFAULT_FEATURE_SET_VERSION"]
