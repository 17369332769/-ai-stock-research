---
version: alpha
name: A-Share Research Workspace
description: A compact, evidence-first financial research interface based on Ant Design principles.
colors:
  brand: "#0B5BD3"
  brand-hover: "#0847A6"
  brand-soft: "#EAF2FF"
  background: "#F3F6FA"
  surface: "#FFFFFF"
  surface-subtle: "#F8FAFC"
  border: "#DCE3EB"
  border-strong: "#C5D0DC"
  text: "#172033"
  text-muted: "#566477"
  up: "#B42318"
  down: "#087443"
  warning: "#7A4500"
  ai: "#5B4AB1"
  ai-soft: "#F4F1FF"
typography:
  page-title:
    fontFamily: "system-ui, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 26px
    fontWeight: 700
    lineHeight: 1.3
  section-title:
    fontFamily: "system-ui, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 16px
    fontWeight: 650
    lineHeight: 1.4
  body:
    fontFamily: "system-ui, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  caption:
    fontFamily: "system-ui, PingFang SC, Microsoft YaHei, sans-serif"
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.5
rounded:
  sm: 6px
  md: 10px
  lg: 14px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  xxl: 32px
components:
  button-height: 36px
  input-height: 38px
  table-row-height: 52px
  content-max-width: 1320px
---

# A股 AI 研究助手设计规范

## Overview

这是一个专业、克制、可信的金融研究工作台。界面服务于需要快速扫描大量行情、判断数据可靠性并追溯研究证据的用户。视觉设计应当高信息密度但不拥挤，以明确层级、稳定对齐和即时状态反馈建立信任。

基础语言参考 Ant Design 的企业级设计原则，但不直接复制通用后台模板。产品应当具有金融研究工具的精确感，而不是营销网站、交易娱乐平台或聊天机器人。

## Colors

- 品牌蓝只用于主要操作、当前导航、选中状态和可交互链接。
- A股语境固定使用红色表示上涨、绿色表示下跌。涨跌文字必须带正负号，不能只依赖颜色。
- 警告、失败、数据过期和模型不可用必须使用各自语义色，不得使用涨跌色代替系统状态色。
- AI 生成内容使用低饱和蓝紫色作为来源标识；不得用 AI 色表达涨跌或投资方向。
- 页面背景为冷灰色，主要内容面为白色，层级主要依靠边框、背景明度和间距，不依赖重阴影。

## Typography

- 全站使用系统中文字体栈，正文基准为 14px。
- 一个页面最多使用四级字号：12px 辅助信息、14px 正文、16px 区块标题、26px 页面标题。
- 行情、金额、百分比、时间和模型指标使用 `font-variant-numeric: tabular-nums`。
- 价格和百分比列右对齐；标签与状态文字左对齐。
- 避免全大写英文标题和装饰性字体。中文任务名称优先。

## Layout

- 使用 4px 基础网格；常用间距为 8、12、16、24、32px。
- 桌面内容最大宽度 1320px，研究表格优先获得横向空间。
- 顶部导航适用于当前少量一级页面，不引入侧边栏。
- 页面首屏顺序固定为：页面上下文、主要筛选、可点击摘要、核心数据表格、次级系统信息。
- 股票详情顺序固定为：股票与行情、状态提示、走势、事件分析、预测、来源文档、历史类比、模型验证。
- 手机端重新组织信息为卡片，不把桌面宽表简单缩小。

## Elevation & Depth

- 默认内容区只使用 1px 边框和极轻阴影。
- 浮层、下拉面板和粘性页内导航可以使用中等阴影。
- 禁止在常规卡片上叠加多层阴影、发光描边或悬浮玻璃效果。
- Hover 最多上移 1px，并必须尊重 `prefers-reduced-motion`。

## Shapes

- 输入框、按钮和普通容器使用 6–10px 圆角。
- 状态徽标、筛选标签可以使用全圆角。
- 大型内容卡片不得使用夸张的 20px 以上圆角。
- 同一层级组件必须共享圆角和边框规则。

## Components

### Navigation

- 品牌、研究池、模型表现、系统状态构成一级导航。
- 当前页面使用蓝色文字和淡蓝背景双重提示，不仅依靠下划线。
- 系统状态入口带小型状态圆点，但不能伪造实时健康状态。

### Research controls

- 搜索是首要控件，范围切换和添加关注紧邻搜索。
- 高级筛选保持次级视觉权重；生效筛选使用可移除标签展示。
- 输入控件必须有可见标签、Hover、Focus、Disabled 和 Error 状态。

### Summary cards

- 只用于上涨、下跌、行情异常、事件股票和待处理研究等高价值摘要。
- 数字是视觉主体，说明文字保持简短。
- 整卡可点击时必须有 Hover、Focus 和选中状态。

### Tables

- 表格是桌面端的主要数据表达形式；表头吸顶，股票列固定。
- 表头使用弱背景，行 Hover 使用极浅品牌蓝。
- 行情数值右对齐并使用等宽数字。
- 无数据、加载、失败、延迟和过期必须是不同状态。

### Sections

- 一个区块只表达一个研究主题。
- 区块标题、辅助说明和操作保持固定对齐。
- 详情页区块序号可见，但视觉权重低于标题。

### AI analysis

- AI 分析是证据卡片，不是聊天气泡。
- 必须展示数据截止时间、影响期限、风险提示、证据来源和模型名称。
- 没有可验证来源时明确写明原因未知，不生成貌似确定的解释。
- 可以展示数据获取、检索、验证和生成摘要等执行状态，但不展示模型内部原始思维链。

### Charts

- 图表优先保证坐标、单位、数据截止时间和涨跌语义清晰。
- 图表颜色与全站涨跌色保持一致；多序列使用低饱和分类色。
- 图表必须有文本摘要或数据表作为可访问替代。

## Do's and Don'ts

### Do

- 优先展示数据是否新鲜、来源是否可靠以及结论的适用时间。
- 使用真实、具体的金融研究文案。
- 让键盘焦点、加载、空状态、错误和恢复操作始终可见。
- 在所有断点检查内容层级、触控尺寸和横向溢出。
- 修改界面时复用现有组件与设计 Token。

### Don't

- 不使用玻璃拟态、大面积渐变、霓虹光效、3D 装饰或营销型 Hero。
- 不把 AI 功能设计成占据整个页面的聊天窗口。
- 不用颜色作为唯一的信息载体。
- 不用历史收盘价冒充实时行情，不隐藏数据年龄或失败原因。
- 不新增未经 Token 定义的随意颜色、间距、圆角或阴影。
- 不为了视觉简洁隐藏模型版本、数据截止时间、风险和证据来源。
