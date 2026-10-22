"""Folium图层与Streamlit表格适配；几何和统计由battlefield统一生成。"""

from html import escape
import folium

TERRAIN_COLORS = {"ridge": "#b87940", "river": "#39a9d1", "road": "#c6bb99",
                  "woods": "#4d9362", "urban": "#a398bd", "fortification": "#ddac50"}
STATUS_LABELS = {"open": "几何未切断", "cut": "几何受阻", "threatened": "受威胁", "unknown": "通行未核实"}


def _tooltip(text):
    # Folium把提示内容放在JS模板字符串内，同时处理HTML和模板插值字符。
    return escape(text).replace("\\", "&#92;").replace("`", "&#96;").replace("${", "&#36;{")


def draw_overlays(m, view):
    terrain = folium.FeatureGroup(name="地形要素（战局记录）", show=True).add_to(m)
    ranges = folium.FeatureGroup(name="火力射程（非杀伤范围）", show=False).add_to(m)
    sectors = folium.FeatureGroup(name="已知射界（未算遮挡）", show=True).add_to(m)
    supply = folium.FeatureGroup(name="补给节点与路线", show=True).add_to(m)
    for f in view["terrain"]["features"]:
        color = TERRAIN_COLORS.get(f["kind"], "#a8a8a8")
        tip = _tooltip(f"{f['name']} · {f['source']}")
        if f["elevation_m"] is not None:
            tip += f" · 标注高程 {f['elevation_m']:g} m"
        if f["geometry"] == "polygon":
            folium.Polygon(f["coordinates"], color=color, weight=2, fill_opacity=.16, tooltip=tip).add_to(terrain)
        elif f["geometry"] == "point":
            folium.CircleMarker(f["coordinates"][0], radius=5, color=color, tooltip=tip).add_to(terrain)
        else:
            folium.PolyLine(f["coordinates"], color=color, weight=3, tooltip=tip).add_to(terrain)
    for r in view["fire_ranges"]:
        tip = _tooltip(f"{r['name']} · 射程 {r['min_range_m']:g}—{r['max_range_m']:g} m · {r['source']}；非杀伤半径")
        folium.Circle(r["position"], radius=r["max_range_m"], color="#da6c67" if r["team"] == "red" else "#629be8",
                      weight=1, fill=False, dash_array="6 5" if r["estimated"] else None, tooltip=tip).add_to(ranges)
        if r["min_range_m"]:
            folium.Circle(r["position"], radius=r["min_range_m"], color="#d9a648", weight=1,
                          fill=False, dash_array="2 5", tooltip="最小射程边界").add_to(ranges)
    for s in view["fields_of_fire"]:
        folium.Polygon(s["coordinates"], color="#da6c67" if s["team"] == "red" else "#629be8",
                       weight=1, fill_opacity=.10,
                       tooltip=_tooltip(f"{s['name']} · 射界 {s['bearing_deg']:g}° / {s['arc_deg']:g}° · 未计算高程遮挡")).add_to(sectors)
    for n in view["supply_nodes"]:
        folium.Marker(n["position"], icon=folium.DivIcon(html="<b style='background:#222;color:#f0c777;padding:3px'>▣</b>"),
                      tooltip=_tooltip(f"{n['name']} · 补给节点 · {n['source']}")).add_to(supply)
    for r in view["supply_lines"]:
        color = "#e05a4e" if r["status"] == "cut" else "#e0a83e" if r["estimated"] or r["status"] != "open" else "#4caf7d"
        tip = f"{r['name']} · {STATUS_LABELS.get(r['status'], '通行未核实')} · {r['source']}"
        if r["cut_by"]:
            tip += " · 几何截断单位：" + str(r["cut_by"])
        folium.PolyLine(r["coordinates"], color=color, weight=2, dash_array="7 6" if r["estimated"] else None,
                       tooltip=_tooltip(tip)).add_to(supply)


def percent(value):
    return "未记录" if value is None else f"{value * 100:.1f}%"


def loss_table_rows(view):
    rows = []
    for row in view["losses"]["units"]:
        p = row["personnel"] or {}
        rows.append({"方": "红" if row["team"] == "red" else "蓝", "单位": row["name"],
                     "累计投入人数": p.get("committed"), "现有人数": p.get("current"),
                     "战斗减员": p.get("casualties"), "非战斗减员": p.get("noncombat_losses"),
                     "战斗减员率": percent(p.get("loss_rate")), "记录状态": row["status"]})
    return rows


def equipment_table_rows(view):
    return [{"方": "红" if row["team"] == "red" else "蓝", "单位": row["name"],
             "装备": e["name"], "计量单位": e["unit"], "累计投入": e["committed"],
             "可用": e["operational"], "待修": e["damaged"], "永久损失": e["destroyed"],
             "永久损失率": percent(e["loss_rate"]), "依据": e["source"]}
            for row in view["losses"]["units"] for e in row["equipment"]]
