"""已收录战役的地理参考锚点。

原先硬编码在 Gentactix.py 顶部的 STRATEGIC_NODES，现独立成模块，
供 prompts（解锁提示）与 geo（锚点吸附清洗）共用，避免口径漂移。
"""

import json
from pathlib import Path

from . import adjudication_lib

# 🗄️ PHSL 绝密战略数据库：高精坐标锚点
# ==========================================
STRATEGIC_NODES = {
    "珍珠港": {
        # 🌟 细化：战列舰大街各核心舰艇高精锚点 (F码头)
        "福特岛-战列舰大街-内华达号": [21.3680, -157.9472],
        "福特岛-战列舰大街-亚利桑那号": [21.3650, -157.9500],
        "福特岛-战列舰大街-田纳西号": [21.3631, -157.9523],
        "福特岛-战列舰大街-马里兰号": [21.3615, -157.9542],
        "福特岛-战列舰大街-加利福尼亚号": [21.3589, -157.9573],

        # 🌟 细化：其他关键被袭舰艇停泊点
        "造船厂-1号干船坞-宾夕法尼亚号": [21.3524, -157.9499],
        "福特岛-西北侧靶舰区-犹他号": [21.3671, -157.9625],

        # 保留原有的宏观战术水道和警戒区
        "内港-西水道": [21.3461, -157.9715],
        "外海-拦截警戒区": [21.2855, -157.9702]
    },
    "马六甲": {
        "新加坡海峡咽喉": [1.196, 103.823],
        "皇京港外海": [2.164, 102.215]
    },
    "中途岛": {
        "环礁核心防空圈": [28.210, -177.376]
    },
    "波斯湾": {
        "霍尔木兹海峡-最窄咽喉": [26.5667, 56.2500],
        "阿巴斯港 (伊方海防核心)": [27.1400, 56.0600],
        "巴林-美第五舰队司令部": [26.2167, 50.6200],
        "阿拉伯海-美航母打击群游弋区": [23.5000, 60.5000],
        "格什姆岛 (伊方导弹阵地)": [26.7500, 55.8300]
    },
    # 🏯 长平之战（前 262—前 260）：冷兵器陆战，锚点按「赵军防线 → 后方指挥 → 秦军进攻轴线」
    #    的空间顺序排列，便于 AI 直接照着摆阵，而不是把十二个单位撒在同一个点上。
    #    坐标来源分两类，已逐条标注：
    #      [文献] 古战场考察给出的 DMS（度分秒），换算成十进制度，精度约 ±50 m；
    #      [POI]  地图服务商收录的现存地名/景区点位，反映「今天这个名字在哪」，精度约 ±200 m。
    #    ⚠️ 两千三百年间河道摆动、村落迁移，古垒原址与今地名有数百米级偏差，够推演用，不够考古用。
    "长平": {
        # —— 赵军丹河防线：北起长平关，南沿丹河河谷 ——
        # 丹朱岭是长平关西山头（海拔 1131 m），与关隘同处一个隘口、直线不到 1 km，
        # 单列会造成两个锚点叠在一起，故并入长平关，不再单设。
        "长平-赵军防线-长平关": [35.9694, 112.7806],      # [文献] 112°46′50″E / 35°58′10″N，海拔 1100 m，赵军第二道防线北端
        "长平-丹河河谷-长平村": [35.9006, 112.8556],      # [文献] 112°51′20″E / 35°54′02″N，长平命名地，村西 150 m 即丹河
        # —— 赵军后方：指挥与屯粮 ——
        "长平-赵军后方-大粮山": [35.7876, 112.9790],      # [POI]  米山镇大粮山，廉颇幕府 + 屯粮处，主峰 1009 m
        "长平-赵军后方-韩王山": [35.8611, 112.9181],      # [文献] 112°55′05″E / 35°51′40″N，赵括决战幕府、赵军被围中心
        # —— 秦军进攻轴线：由西南空仓岭东进，诱赵军出壁垒后包抄 ——
        "长平-秦军进攻轴线-空仓岭": [35.7306, 112.7348],  # [POI]  马村镇老马岭（高平关），秦军假粮仓/初战处，海拔 1188 m
    },
}


KOREAN_ANCHORS = json.loads(
    Path(__file__).with_name("korean_anchors.json").read_text(encoding="utf-8"))
NODE_METADATA = {point["name"]: point for point in KOREAN_ANCHORS["points"].values()}
for _battle, _entry in KOREAN_ANCHORS["campaigns"].items():
    STRATEGIC_NODES[_battle] = {
        KOREAN_ANCHORS["points"][key]["name"]:
        KOREAN_ANCHORS["points"][key]["coordinates"]
        for key in _entry["points"]
    }


def node_metadata(name):
    """无资料时返回空；不把旧库未注明的精度伪装成已核定数据。"""
    return NODE_METADATA.get(name, {})


def collect_available_nodes(scenario_input, battle=None):
    """按同一战役解析器选择九场朝鲜战争锚点；其余沿用旧战区匹配。

    显式选择优先、别名取最长，不因泛称“抗美援朝”混入全部九场。
    返回独立坐标列表，调用方无法修改源库。
    """
    text = str(scenario_input or "")
    selected = adjudication_lib.resolve(text, battle=battle).name
    if selected in KOREAN_ANCHORS["campaigns"]:
        return {name: list(coords) for name, coords in STRATEGIC_NODES[selected].items()}
    available_nodes = {}
    for key, nodes in STRATEGIC_NODES.items():
        if key not in KOREAN_ANCHORS["campaigns"] and key in text:
            available_nodes.update({name: list(coords) for name, coords in nodes.items()})
    return available_nodes
