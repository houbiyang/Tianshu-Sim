// 🗺️ 战术地图渲染语义：gentactix/viz.py 的 TypeScript 移植。
// 军标、行军轨迹、交战半径推导、定向扇区/降级空心环的口径与 Python 渲染层
// 保持一致；改这里必须同步对照 viz.py，两扇门共用同一套地图语言。

import type { Engagement, Unit, WarState } from "./types";
import unitProfiles from "../../gentactix/unit_types.json";

export function classifyUnit(unitType: unknown) {
  const text = String(unitType ?? "").trim().toLowerCase();
  return unitProfiles.find((p) => p.keywords.some((k) => text.includes(k)));
}

// 渲染器版本标记：界面上直接显示，用来区分「代码没生效」和「本回合确实没有交战」。
export const RENDERER_VERSION = "sector-v3-vue";
export function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]!));
}

// 底图图层组（按实测可达性排序；高德 GCJ-02 偏移，刻意隐藏，与 viz.py 同口径）
export interface TileLayerCfg {
  name: string;
  show: boolean;
  url: string;
  attr: string;
  maxNativeZoom: number;
  enabled?: boolean;
}

export const TILE_LAYERS: TileLayerCfg[] = [
  { name: "🛰️ 天眼卫星实景", show: true,
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr: "Esri World Imagery", maxNativeZoom: 18 },
  { name: "🌊 海洋底图", show: false,
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
    attr: "Esri Ocean Base", maxNativeZoom: 10 },
  // Esri 地形在中国大陆只到 z13，更高缩放放大 z13 瓦片而不是整片空白
  { name: "🏔️ 战术地形图", show: false,
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    attr: "Esri World Topo", maxNativeZoom: 13 },
  { name: "🗺️ OpenTopoMap（国内网络通常不可达）", show: false,
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    attr: "OpenTopoMap", maxNativeZoom: 17 },
  // 🇨🇳 高德瓦片是 GCJ-02，与 WGS-84 军标叠加有数百米偏移 —— 保持隐藏
  { name: "🇨🇳 高德路网（GCJ-02，点位会有数百米偏移）", show: false,
    url: "https://webrd01.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
    attr: "高德地图", maxNativeZoom: 18, enabled: false },
];

// ==========================================
// 🌟 北约 APP-6 风格军标（与 get_nato_html 同一套 HTML）
// ==========================================
function unitTypeOf(unit: Unit): string {
  return String(unit.type ?? unit["类型"] ?? unit["兵种"] ?? unit.unit_type ?? unit.class ?? "").toLowerCase();
}

function unitNameOf(unit: Unit): string {
  return String(unit.name ?? unit["名称"] ?? unit["单位名称"] ?? unit["部队名称"] ?? unit.unit_name ?? "未知单位");
}

export function natoSymbol(unit: Unit): string {
  const t = unitTypeOf(unit);
  const profile = classifyUnit(t);
  if (profile) {
    return profile.symbol === "armor"
      ? "<div style='width: 14px; height: 6px; border: 2px solid black; border-radius: 50%;'></div>"
      : profile.symbol;
  }
  let symbol = "?";
  if (["步兵", "陆战", "军团", "师", "旅", "infantry", "marine", "troop", "army"].some((k) => t.includes(k))) {
    symbol = "X";
  } else if (["坦克", "装甲", "战车", "机动", "骑兵", "tank", "armor", "vehicle", "cavalry"].some((k) => t.includes(k))) {
    symbol = "<div style='width: 14px; height: 6px; border: 2px solid black; border-radius: 50%;'></div>";
  } else if (["炮", "防空", "导弹", "火力", "artillery", "missile", "anti-air"].some((k) => t.includes(k))) {
    symbol = "●";
  } else if (["机", "轰炸", "航空", "飞行", "plane", "fighter", "bomber", "air"].some((k) => t.includes(k))) {
    symbol = "∞";
  } else if (["舰", "船", "航母", "潜艇", "巡洋", "驱逐", "ship", "carrier", "submarine", "destroyer", "cruiser", "fleet"].some((k) => t.includes(k))) {
    symbol = "⚓";
  } else if (["指挥", "基地", "总部", "阵地", "hq", "base", "command", "center"].some((k) => t.includes(k))) {
    symbol = "★";
  }
  return symbol;
}

export function natoHtml(unit: Unit, team: "red" | "blue"): string {
  const symbol = natoSymbol(unit);
  const hp = toFloat(unit.hp ?? unit["兵力"]);
  const inactive = hp !== null && hp <= 0;
  const unitName = escapeHtml(unitNameOf(unit));
  const frame = team === "red"
    ? `<div style="position: relative; width: 30px; height: 30px;">
        <div style="position: absolute; top: 3px; left: 3px; width: 22px; height: 22px;
                    background: rgba(255, 180, 180, 0.85); border: 2px solid #cc0000;
                    transform: rotate(45deg); box-shadow: 2px 2px 5px rgba(0,0,0,0.5);"></div>
        <div style="position: absolute; top: 0; left: 0; width: 30px; height: 30px;
                    display: flex; align-items: center; justify-content: center;
                    color: black; font-weight: bold; font-size: 20px; font-family: sans-serif; z-index: 10;">
            ${symbol}
        </div>
    </div>`
    : `<div style="width: 36px; height: 22px;
        background: rgba(180, 210, 255, 0.85); border: 2px solid #0044cc;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.5);
        display: flex; align-items: center; justify-content: center;
        color: black; font-weight: bold; font-size: 20px; font-family: sans-serif;">
        ${symbol}
    </div>`;
  return `<div class="${inactive ? "unit-inactive" : "unit-active"}" style="${inactive ? "filter: grayscale(1); opacity: .65;" : ""} display: flex; flex-direction: column; align-items: center; width: 120px; margin-left: -60px; margin-top: -15px;">
    ${frame}
    <div class="unit-name" style="background: rgba(0,0,0,0.7); color: #fff; font-size: 11px; padding: 2px 5px; border-radius: 3px; margin-top: 3px; font-family: sans-serif; white-space: nowrap; border: 1px solid rgba(255,255,255,0.2);">
        ${unitName}${inactive ? " · 已失能" : ""}
    </div>
</div>`;
}

export function unitPopup(unit: Unit): string {
  const hp = unit.hp ?? unit["兵力"] ?? "未知";
  const morale = toFloat(unit.morale) ?? 100.0;
  const supply = toFloat(unit.supply) ?? 100.0;
  const moraleIcon = morale > 50 ? "🔥" : "⚠️";
  const supplyIcon = supply > 50 ? "🔋" : "🪫";
  return `<b>${escapeHtml(unitNameOf(unit))}</b><br>类型: ${escapeHtml(unitTypeOf(unit) || "?")}<br>HP: ❤️ ${escapeHtml(hp)}` +
    ((toFloat(hp) ?? 1) <= 0 ? '<br>已失能：保留历史位置，不参与地面几何裁决' : '') +
    `<br>士气: ${moraleIcon} ${morale}%<br>补给: ${supplyIcon} ${supply}%`;
}

// ==========================================
// 🎯 交战波及半径：按攻击单位射程推导（与 viz.py 同口径）
// ==========================================
export const ENGAGEMENT_RADIUS_FACTOR = 1.2;

const UNIT_TYPE_RANGE_HINTS: [string, number][] = [
  ["骑射", 800.0], ["弓骑", 800.0], ["mounted archer", 800.0], ["horse archer", 800.0],
  ["投石", 400.0], ["弩", 300.0], ["弓", 300.0], ["archer", 300.0], ["bow", 300.0],
  ["步", 500.0], ["infantry", 500.0], ["战车", 500.0], ["chariot", 500.0],
  ["骑兵", 500.0], ["cavalry", 500.0],
];
export const DEFAULT_MELEE_RANGE = 500.0;
export const NEUTRAL_ENGAGEMENT_RADIUS = 1500.0;
const MIN_ENGAGEMENT_RADIUS = 50.0;
const MAX_ENGAGEMENT_RADIUS = 50000.0;
const MAX_ATTACKER_MATCH_DEG = 0.15;

export function toFloat(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function clampRadius(r: number): number {
  return Math.max(MIN_ENGAGEMENT_RADIUS, Math.min(MAX_ENGAGEMENT_RADIUS, r));
}

export function unitTypeDefaultRange(unitType: string): number | null {
  const text = String(unitType ?? "").trim().toLowerCase();
  const profile = classifyUnit(text);
  if (profile) return profile.range_hint_m;
  if (!text) return null;
  for (const [keyword, range] of UNIT_TYPE_RANGE_HINTS) {
    if (text.includes(keyword)) return range;
  }
  return null;
}

function unitDistanceDeg(unit: Unit, lat: number, lng: number): number | null {
  const uLat = toFloat(unit.lat ?? unit["纬度"]);
  const uLng = toFloat(unit.lng ?? unit["经度"]);
  if (uLat === null || uLng === null) return null;
  const k = Math.max(0.1, Math.cos((Math.abs(lat) * Math.PI) / 180));
  return Math.hypot(uLat - lat, (uLng - lng) * k);
}

function nearestUnit(units: Unit[], lat: number, lng: number, maxDeg = MAX_ATTACKER_MATCH_DEG): Unit | null {
  let best: Unit | null = null;
  let bestDist: number | null = null;
  for (const unit of units) {
    const dist = unitDistanceDeg(unit, lat, lng);
    if (dist === null || dist > maxDeg) continue;
    if (bestDist === null || dist < bestDist) {
      best = unit;
      bestDist = dist;
    }
  }
  return best;
}

export function findAttackerUnit(battle: Engagement, warState: WarState, lat: number, lng: number): Unit | null {
  const units: Unit[] = [];
  for (const key of ["red_units", "blue_units"] as const) {
    const team = warState[key];
    if (Array.isArray(team)) units.push(...(team as Unit[]));
  }
  if (units.length === 0) return null;

  const rawName = battle.attacker ?? battle["attacker_name"] ?? battle["攻击方"];
  if (typeof rawName === "string" && rawName.trim()) {
    const target = rawName.trim();
    for (const unit of units) {
      const name = unit.name ?? unit["名称"] ?? unit.unit_name;
      if (String(name ?? "").trim() === target) return unit;
    }
  }

  const aPos = battle.attacker_pos ?? battle["攻击者位置"] ?? battle["攻击方坐标"];
  if (Array.isArray(aPos) && aPos.length === 2) {
    const aLat = toFloat(aPos[0]);
    const aLng = toFloat(aPos[1]);
    if (aLat !== null && aLng !== null) {
      const unit = nearestUnit(units, aLat, aLng);
      if (unit) return unit;
    }
  }
  return nearestUnit(units, lat, lng);
}

export interface RadiusResolution {
  radius: number;
  basis: string;
}

export function resolveEngagementRadius(battle: Engagement, warState: WarState, lat: number, lng: number): RadiusResolution {
  const unit = findAttackerUnit(battle, warState, lat, lng);
  if (unit) {
    const unitName = String(unit.name ?? unit["名称"] ?? unit.unit_name ?? "攻击方");
    const unitType = unitTypeOf(unit);
    const attackRange = toFloat(unit.attack_range ?? unit["射程"] ?? unit["攻击范围"] ?? unit["打击范围"] ?? unit.range);
    if (attackRange !== null && attackRange > 0) {
      return { radius: clampRadius(attackRange * ENGAGEMENT_RADIUS_FACTOR),
               basis: `${unitName} 射程 ${attackRange}m × ${ENGAGEMENT_RADIUS_FACTOR}` };
    }
    const hint = unitTypeDefaultRange(unitType);
    if (hint !== null) {
      const label = unitType ? `${unitName}·${unitType}` : unitName;
      return { radius: clampRadius(hint * ENGAGEMENT_RADIUS_FACTOR),
               basis: `${label} 按兵种射程 ${hint}m × ${ENGAGEMENT_RADIUS_FACTOR}` };
    }
    return { radius: clampRadius(DEFAULT_MELEE_RANGE * ENGAGEMENT_RADIUS_FACTOR),
             basis: `${unitName} 射程与兵种均未知，按步战推定 ${DEFAULT_MELEE_RANGE}m × ${ENGAGEMENT_RADIUS_FACTOR}` };
  }
  const explicit = toFloat(battle.radius);
  if (explicit !== null && explicit > 0) {
    return { radius: clampRadius(explicit), basis: "未匹配到攻击单位，沿用战局给出的半径" };
  }
  return { radius: clampRadius(NEUTRAL_ENGAGEMENT_RADIUS), basis: "未匹配到攻击单位，使用中性默认值" };
}

// ==========================================
// 🌟 定向扇区：圆心是攻击方，方位角从正北顺时针（与 impact_sector_vertices 一致）
// ==========================================
export function impactSectorVertices(
  originLat: number, originLng: number, targetLat: number, targetLng: number,
  radiusM: number, halfAngleDeg = 30.0, stepDeg = 5.0,
): [number, number][] {
  const bearing = (Math.atan2(targetLng - originLng, targetLat - originLat) * 180) / Math.PI;
  const rLat = radiusM / 111320.0;
  const rLng = radiusM / (111320.0 * Math.max(0.1, Math.cos((Math.abs(originLat) * Math.PI) / 180)));
  const steps = Math.max(1, Math.round((2 * halfAngleDeg) / stepDeg));
  const pts: [number, number][] = [[originLat, originLng]];
  for (let i = 0; i <= steps; i++) {
    const angle = ((bearing - halfAngleDeg + i * stepDeg) * Math.PI) / 180;
    pts.push([originLat + rLat * Math.cos(angle), originLng + rLng * Math.sin(angle)]);
  }
  pts.push([originLat, originLng]);
  return pts;
}

export interface EngagementSpec {
  valid: boolean;
  reason?: string;
  lat?: number;
  lng?: number;
  desc?: string;
  radius?: number;
  basis?: string;
  useSector?: boolean;
  attacker?: [number, number];
}

// 单一入口：绘制与统计都走这里，非法条目带原因而不是静默消失
export function* iterEngagementSpecs(warState: WarState): Generator<EngagementSpec> {
  const engagements = Array.isArray(warState.engagements) ? warState.engagements : [];
  for (const battle of engagements) {
    if (!battle || typeof battle !== "object") {
      yield { valid: false, reason: "条目不是对象" };
      continue;
    }
    const bLat = toFloat(battle.lat ?? battle["纬度"]);
    const bLng = toFloat(battle.lng ?? battle["经度"]);
    if (bLat === null || bLng === null) {
      yield { valid: false, reason: "缺少可用经纬度" };
      continue;
    }
    const desc = String(battle.description ?? battle["描述"] ?? battle["交战描述"] ?? battle["战况"] ?? "激烈交火区域");
    const { radius, basis } = resolveEngagementRadius(battle, warState, bLat, bLng);

    const attackerRaw = battle.attacker_pos ?? battle["攻击者位置"] ?? battle["攻击方坐标"];
    let aLat: number | null = null;
    let aLng: number | null = null;
    if (Array.isArray(attackerRaw) && attackerRaw.length === 2) {
      aLat = toFloat(attackerRaw[0]);
      aLng = toFloat(attackerRaw[1]);
    }
    // 攻击方缺失/非法/与交战点重合 ⇒ 降级空心虚线环；绝不猜方向硬画扇区
    const sameAsTarget = aLat !== null && aLng !== null &&
      Math.abs(aLat - bLat) < 1e-9 && Math.abs(aLng - bLng) < 1e-9;
    const useSector = aLat !== null && aLng !== null && !sameAsTarget;

    yield { valid: true, lat: bLat, lng: bLng, desc, radius, basis, useSector,
            attacker: useSector ? [aLat as number, aLng as number] : undefined };
  }
}

export interface EngagementStats {
  total: number; valid: number; skipped: number; sector: number; ring: number; reasons: string[];
}

export function summarizeEngagements(warState: WarState): EngagementStats {
  const stats: EngagementStats = { total: 0, valid: 0, skipped: 0, sector: 0, ring: 0, reasons: [] };
  for (const spec of iterEngagementSpecs(warState)) {
    stats.total += 1;
    if (!spec.valid) {
      stats.skipped += 1;
      stats.reasons.push(spec.reason ?? "未知原因");
      continue;
    }
    stats.valid += 1;
    if (spec.useSector) stats.sector += 1; else stats.ring += 1;
  }
  return stats;
}

// 🔧 渲染自检：只在渲染层追加一个虚构交战（副本），不改战局数据
export function withDemoEngagement(warState: WarState): WarState {
  const copy: WarState = { ...warState };
  const center = copy.center as [number, number] | undefined;
  let cLat = 35.70;
  let cLng = 112.93;
  if (Array.isArray(center)) {
    const fLat = toFloat(center[0]);
    const fLng = toFloat(center[1]);
    if (fLat !== null && fLng !== null) { cLat = fLat; cLng = fLng; }
  }
  copy.engagements = [...(copy.engagements ?? []), {
    lat: cLat, lng: cLng + 0.012,
    description: "🧪 渲染自检示例（虚构交战，不写入战局）",
    attacker: "自检攻击方",
    attacker_pos: [cLat - 0.010, cLng - 0.004],
    radius: 2500,
  } as Engagement];
  return copy;
}

// 半径分级配色（与 build_tactical_map 的 tiers 一致）
export function engagementColors(radiusM: number): { border: string; fill: string } {
  if (radiusM >= 20000) return { border: "black", fill: "purple" };
  if (radiusM >= 5000) return { border: "darkred", fill: "crimson" };
  if (radiusM <= 500) return { border: "darkorange", fill: "orange" };
  return { border: "crimson", fill: "red" };
}
