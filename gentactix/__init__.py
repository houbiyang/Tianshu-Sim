"""GenTactix · 战术推演引擎包。

模块划分：
- config : 引擎常量、模型回退链、通用工具（to_float / is_naval）
- anchors: 高精历史坐标锚点库 STRATEGIC_NODES
- geo    : 双波段海陆雷达、强制落水、坐标校验、锚点吸附清洗
- engine : DeepSeek 三脑调用（推演引擎 A / 参谋长复盘 B / 璇玑问答 C）
- prompts: 初始化与单回合推演的 Prompt 模板
- state  : Session State 默认值与单位字段提取助手
- viz    : folium 战术地图渲染（北约军标 + 轨迹 + 交战圈）
"""

__version__ = "2.0.0"
