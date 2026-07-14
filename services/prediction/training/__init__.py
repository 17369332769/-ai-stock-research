"""训练层（spec §9.3 / §9.3.1 / §9.4）。

样本回放 → walk-forward → LightGBM（回归 + 方向）→ 概率校准 → 产物 → 注册 candidate。

不做 re-export，原因见 ``services/prediction/features/__init__.py`` 的说明（跨子包循环导入）。
"""
