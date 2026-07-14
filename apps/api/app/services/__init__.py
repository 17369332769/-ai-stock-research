"""业务编排层。

只做「权限边界、输入校验、DTO 和业务编排」；
**不得包含训练算法或采集解析器**（spec §5.1）—— 那些属于 services/prediction 与 services/market_data。
"""
