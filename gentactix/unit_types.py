"""近现代兵种分类与工作尺度；Python裁决和两套地图共享JSON，纯标准库。

速度是战术位移的估计基准，射程是缺字段时的显示兜底，均不是装备性能表。
航空/舰艇支援不参加地面几何；间瞄火力和保障分队不凭射程封锁土地。
"""

import json
from pathlib import Path

UNIT_PROFILES = tuple(json.loads(Path(__file__).with_name("unit_types.json").read_text(encoding="utf-8")))
ARMOR_SYMBOL = "<div style='width: 14px; height: 6px; border: 2px solid black; border-radius: 50%;'></div>"


def classify_unit(unit_type):
    """具体分类优先，避免机械化步兵落入步兵、机枪被单字“机”认作航空。"""
    text = str(unit_type or "").strip().lower()
    return next((p for p in UNIT_PROFILES if any(k in text for k in p["keywords"])), None)


def unit_symbol(unit_type):
    profile = classify_unit(unit_type)
    if profile is None:
        return None
    return ARMOR_SYMBOL if profile["symbol"] == "armor" else profile["symbol"]


def modern_unit_instruction(context):
    if not any(k in str(context) for k in ("抗美援朝", "朝鲜战争", "上甘岭", "长津湖")):
        return ""
    labels = "、".join(p["label"] for p in UNIT_PROFILES)
    return f"""
        【近现代兵种与历史适用性】type优先使用：{labels}。
        - 按当时实际配属生成；1950—1953年的“现代分析”不允许添加无人机、卫星、网络战或精确制导武器。
        - 美骑兵第1师的骑兵是历史番号，type应按当时徒步/摩托化步兵与坦克支援填写，不能生成骑马骑射兵。
        - 摩托化表示汽车输送，不等于机械化步兵；运输、医疗、工兵、通信与战斗部队分开，不能重复计入同一兵力。
        - 人数、坦克数、火炮数、航空架次是不同单位，禁止相加作为hp；无统一人数资料时明确为抽象兵力。
        - 攻击射程attack_range按米填写，speed是相对机动提示，不是公路最高速度；保障单位不虚构交战。
        - 航空/舰艇支援保留为支援角色，不按地面徒步速度移动或参加地面合围；炮兵射程不代表占领土地。
        - 非战斗减员、工事、通信、空中遮断和谈判是历史条件；未有独立算法时不得宣称已作确定性计算。
        """
