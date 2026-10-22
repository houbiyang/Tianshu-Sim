<script setup lang="ts">
// 🗺️ 战术地图：gentactix/viz.py build_tactical_map 的 Leaflet 移植。
// 军标、行军轨迹（含 ➤ 箭头与蚂蚁线动画）、交战定向扇区/降级空心环、
// 底图图层组与图层控制台的位置/口径都与 Folium 版一致；改这里请对照 viz.py。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import L from "leaflet";
import offlineLand from '../../public/maps/land-110m.geojson?raw';
import type { Snapshot, Unit } from "../types";
import {
  TILE_LAYERS, natoHtml, unitPopup, iterEngagementSpecs,
  impactSectorVertices, engagementColors, withDemoEngagement, toFloat,
  escapeHtml,
} from "../tactical";

const props = defineProps<{ snap: Snapshot | null; selftest: boolean; overview?: boolean }>();

const el = ref<HTMLDivElement | null>(null);
let map: L.Map | null = null;
let layerGroup: L.LayerGroup | null = null;
let centeredSession: string | null = null;
const visible = reactive({ terrain: true, ranges: true, sectors: true, supply: true });
const view = computed(() => props.snap?.battlefield);
const terrainBase = ref(false);
const showUnitLabels = ref(false);
const overlays: Record<string, L.LayerGroup> = {};
const baseLayers: Record<string, L.TileLayer> = {};
let sizeObserver: ResizeObserver | null = null;

function textTooltip(text: string) {
  const node = document.createElement("span");
  node.textContent = text;
  return node;
}

function toggleTerrainBase() {
  if (!map) return;
  const useTerrain = !terrainBase.value;
  const name = useTerrain ? "🏔️ 战术地形图" : "🛰️ 天眼卫星实景";
  for (const layer of Object.values(baseLayers)) map.removeLayer(layer);
  baseLayers[name]?.addTo(map);
  terrainBase.value = useTerrain;
}

function drawBattlefield() {
  for (const group of Object.values(overlays)) group.clearLayers();
  const data = view.value;
  if (!data) return;
  const terrainColors: Record<string, string> = { ridge: "#b87940", river: "#39a9d1", road: "#c6bb99", woods: "#4d9362", urban: "#a398bd", fortification: "#ddac50" };
  const teamColor = (team: string) => team === "red" ? "#da6c67" : "#629be8";
  if (visible.terrain) for (const f of data.terrain.features) {
    const style = { color: terrainColors[f.kind] ?? "#a8a8a8", weight: 3, className: "bf-terrain", fillOpacity: 0.16 };
    const shape = f.geometry === "polygon" ? L.polygon(f.coordinates, style)
      : f.geometry === "point" ? L.circleMarker(f.coordinates[0], { ...style, radius: 5 })
      : L.polyline(f.coordinates, style);
    shape.bindTooltip(textTooltip(`${f.name} · ${f.source}${f.elevation_m == null ? "" : ` · 标注高程 ${f.elevation_m} m`}`)).addTo(overlays.terrain);
  }
  if (visible.ranges) for (const r of data.fire_ranges) {
    const tip = `${r.name} · 射程 ${r.min_range_m}—${r.max_range_m} m · ${r.source}；非杀伤半径`;
    L.circle(r.position, { radius: r.max_range_m, color: teamColor(r.team), weight: 1, fill: false,
      dashArray: r.estimated ? "6 5" : undefined, className: "bf-range" })
      .bindTooltip(textTooltip(tip)).addTo(overlays.ranges);
    if (r.min_range_m) L.circle(r.position, { radius: r.min_range_m, color: "#d9a648", weight: 1, fill: false,
      dashArray: "2 5", className: "bf-min-range" }).bindTooltip("最小射程边界").addTo(overlays.ranges);
  }
  if (visible.sectors) for (const s of data.fields_of_fire) {
    L.polygon(s.coordinates, { color: teamColor(s.team), weight: 1, fillOpacity: 0.10, className: "bf-sector" })
      .bindTooltip(textTooltip(`${s.name} · 射界 ${s.bearing_deg}° / ${s.arc_deg}° · 未计算高程遮挡`)).addTo(overlays.sectors);
  }
  if (visible.supply) {
    for (const n of data.supply_nodes) {
      L.marker(n.position, { icon: L.divIcon({ className: "supply-node", html: "▣", iconSize: [22, 22], iconAnchor: [11, 11] }) })
        .bindTooltip(textTooltip(`${n.name} · 补给节点 · ${n.source}`)).addTo(overlays.supply);
    }
    const labels: Record<string, string> = { open: "几何未切断", cut: "几何受阻", threatened: "受威胁", unknown: "通行未核实" };
    for (const r of data.supply_lines) {
      const color = r.status === "cut" ? "#e05a4e" : r.estimated || r.status !== "open" ? "#e0a83e" : "#4caf7d";
      L.polyline(r.coordinates, { color, weight: 2, dashArray: r.estimated ? "7 6" : undefined, className: "bf-supply" })
        .bindTooltip(textTooltip(`${r.name} · ${labels[r.status] ?? labels.unknown} · ${r.source}${r.cut_by ? ` · 几何截断单位：${r.cut_by}` : ""}`)).addTo(overlays.supply);
    }
  }
}

function battleCenter(snap: Snapshot | null): [number, number] {
  const center = snap?.war_state?.center;
  if (Array.isArray(center) && center.length === 2) {
    const lat = toFloat(center[0]), lng = toFloat(center[1]);
    if (lat !== null && lng !== null && Math.abs(lat) <= 90 && Math.abs(lng) <= 180) {
      return [lat, lng];
    }
  }
  // 缺少中心时以首个有效单位定位，避免其他战役仍落在长平。
  for (const u of [...(snap?.war_state?.red_units || []), ...(snap?.war_state?.blue_units || [])]) {
    const pos = unitPos(u);
    if (pos && Math.abs(pos[0]) <= 90 && Math.abs(pos[1]) <= 180) return pos;
  }
  return [35.8, 104];
}

function unitPos(u: Unit): [number, number] | null {
  const lat = toFloat(u.lat), lng = toFloat(u.lng);
  if (lat === null || lng === null) return null;
  return [lat, lng];
}

function unitByName(snap: Snapshot, team: "red" | "blue", name: string): Unit | null {
  if (!snap.last_war_state) return null;
  const list = (team === "red" ? snap.last_war_state.red_units : snap.last_war_state.blue_units) || [];
  return list.find((u) => String(u.name ?? "") === name) ?? null;
}

// ➤ 箭头沿行军方向（方位角从正北顺时针，与 folium PolyLineTextPath 同语义）
function arrowAt(from: [number, number], to: [number, number], color: string): L.Marker {
  const mid: [number, number] = [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2];
  const bearing = (Math.atan2(to[1] - from[1], to[0] - from[0]) * 180) / Math.PI;
  return L.marker(mid, {
    interactive: false,
    icon: L.divIcon({
      className: "trail-arrow",
      html: `<span style="color:${color};font-weight:bold;font-size:18px;transform:rotate(${bearing}deg);display:inline-block;">➤</span>`,
      iconSize: [18, 18], iconAnchor: [9, 9],
    }),
  });
}

function drawTrails(snap: Snapshot, team: "red" | "blue", color: string) {
  const units = (team === "red" ? snap.war_state!.red_units : snap.war_state!.blue_units) || [];
  for (const u of units) {
    const pos = unitPos(u);
    if (!pos) continue;
    const prev = unitByName(snap, team, String(u.name ?? ""));
    const prevPos = prev ? unitPos(prev) : null;
    if (!prevPos || (prevPos[0] === pos[0] && prevPos[1] === pos[1])) continue;
    layerGroup!.addLayer(L.polyline([prevPos, pos], {
      color, weight: 2, opacity: 0.6, dashArray: "5 5",
      interactive: true,
    }).bindTooltip(textTooltip(`${u.name ?? ""} 的行军路线`)));
    layerGroup!.addLayer(arrowAt(prevPos, pos, color));
    // 蚂蚁线动画（folium plugins.AntPath 的 CSS 等价实现）
    layerGroup!.addLayer(L.polyline([prevPos, pos], {
      color, weight: 5, opacity: 0.3, className: "ant-march", interactive: false,
    }));
  }
}

function drawUnits(snap: Snapshot, team: "red" | "blue", color: string) {
  const units = (team === "red" ? snap.war_state!.red_units : snap.war_state!.blue_units) || [];
  for (const u of units) {
    const pos = unitPos(u);
    if (!pos) continue;
    const marker = L.marker(pos, {
      riseOnHover: true,
      icon: L.divIcon({ className: "nato-marker", html: natoHtml(u, team), iconSize: null as unknown as undefined }),
    });
    marker.bindPopup(unitPopup(u), { maxWidth: 200 });
    marker.bindTooltip(textTooltip(String(u.name || '未知单位')), { direction: 'right' });
    layerGroup!.addLayer(marker);
  }
}

function drawEngagements(snap: Snapshot) {
  const state = props.selftest && snap.war_state ? withDemoEngagement(snap.war_state) : snap.war_state;
  if (!state) return;
  for (const spec of iterEngagementSpecs(state)) {
    if (!spec.valid) continue;
    const bPos = [spec.lat!, spec.lng!] as [number, number];
    const radius = spec.radius!;
    const { border, fill } = engagementColors(radius);
    const sector = spec.useSector && spec.attacker;
    const tooltip = `💥 <b>${escapeHtml(spec.desc)}</b><br>交战示意范围 ≈ ${radius.toFixed(0)} 米（非杀伤半径）` +
      `<br><span style='color:#8a8a8a'>依据：${escapeHtml(spec.basis)}</span>` +
      (sector ? "" : "<br>（未提供攻击方位，仅显示范围）");

    if (sector) {
      const [aLat, aLng] = spec.attacker!;
      layerGroup!.addLayer(L.polygon(impactSectorVertices(aLat, aLng, spec.lat!, spec.lng!, radius), {
        color: border, weight: 1.5, fill: true, fillColor: fill, fillOpacity: 0.12, dashArray: "6 4",
      }).bindTooltip(tooltip));
    } else {
      layerGroup!.addLayer(L.circle(bPos, {
        radius, color: border, weight: 1.5, fill: false, dashArray: "6 4",
      }).bindTooltip(tooltip));
    }

    layerGroup!.addLayer(
      L.marker(bPos, {
        icon: L.divIcon({ className: "fire-marker", html: "🔥", iconSize: [22, 22], iconAnchor: [11, 11] }),
      }).bindTooltip(`💥 <b>交战发生 (示意 ${radius.toFixed(0)} 米，非杀伤半径)</b><br>${escapeHtml(spec.desc)}`),
    );

    // 火力打击轨迹：扇区已表达方向，这里只指向落点，压细压淡（AntPath 等价）
    if (sector) {
      const [aLat, aLng] = spec.attacker!;
      layerGroup!.addLayer(L.polyline([[aLat, aLng], bPos], {
        color: border, weight: 1.5, opacity: 0.55, className: "ant-fire", interactive: false,
      }).bindTooltip("🔥 火力打击方向"));
    }
  }
}

function render() {
  if (!map || !layerGroup) return;
  layerGroup.clearLayers();
  const snap = props.snap;
  if (!snap?.war_state) return;
  // 新会话重新定位；同一局下一回合和渲染自检保留用户的平移、缩放。
  if (centeredSession !== snap.session_id) {
    const positions = [...(snap.war_state.red_units || []), ...(snap.war_state.blue_units || [])]
      .map(unitPos).filter((position): position is [number, number] => position !== null);
    if (positions.length > 1) map.fitBounds(L.latLngBounds(positions), { padding: [55, 55], maxZoom: 13, animate: false });
    else map.setView(battleCenter(snap), 11, { animate: false });
    centeredSession = snap.session_id;
  }
  drawTrails(snap, "red", "red");
  drawTrails(snap, "blue", "blue");
  drawUnits(snap, "red", "red");
  drawUnits(snap, "blue", "blue");
  drawEngagements(snap);
  drawBattlefield();
}

onMounted(() => {
  if (!el.value) return;
  // 与 build_tactical_map 一致：以战局 center 开局、zoom 11；后续回合只重画图层，
  // 不重置用户缩放（对应 app.py 固定组件 key 的意图）
  map = L.map(el.value, { center: battleCenter(props.snap), zoom: props.snap?.war_state ? 11 : 4, zoomControl: true });
  // Bundled public-domain coastlines keep the China overview usable without tile access.
  map.createPane('offline-land');
  map.getPane('offline-land')!.style.zIndex = '150';
  const activeMap = map;
  L.geoJSON(JSON.parse(offlineLand), { pane: 'offline-land', style: { color: '#51697b', weight: 1, fillColor: '#243b4a', fillOpacity: 1 },
    attribution: 'Offline coastline: Natural Earth (public domain)' }).addTo(activeMap);
  if (props.overview) {
    for (const [name, lat, lng] of [['北京', 39.90, 116.40], ['沈阳', 41.80, 123.43], ['上海', 31.23, 121.47], ['西安', 34.34, 108.94], ['成都', 30.57, 104.07], ['广州', 23.13, 113.26]] as [string, number, number][]) {
      L.circleMarker([lat, lng], { radius: 3, color: '#7fe1ef', weight: 1, fillOpacity: 1 })
        .bindTooltip(textTooltip(name), { permanent: true, direction: 'right', className: 'overview-city' }).addTo(map);
    }
  }
  let firstShown: L.TileLayer | null = null;
  for (const cfg of TILE_LAYERS) {
    if (cfg.enabled === false) continue; // 高德 GCJ-02 偏移：与 Folium 版一样保持隐藏
    const layer = L.tileLayer(cfg.url, {
      attribution: cfg.attr, maxZoom: 19, maxNativeZoom: cfg.maxNativeZoom,
    });
    baseLayers[cfg.name] = layer;
    if (cfg.show) { layer.addTo(map); firstShown = layer; }
  }
  if (!firstShown) baseLayers[TILE_LAYERS[0].name]?.addTo(map);
  L.control.layers(baseLayers, {}, { position: "topright", collapsed: true }).addTo(map);
  map.on("baselayerchange", (e: L.LayersControlEvent) => { terrainBase.value = e.name === "🏔️ 战术地形图"; });
  for (const name of Object.keys(visible)) overlays[name] = L.layerGroup().addTo(map);

  // 全屏展开（folium plugins.Fullscreen 等价）
  const Fullscreen = L.Control.extend({
    options: { position: "topleft" },
    onAdd: () => {
      const btn = L.DomUtil.create("a", "leaflet-control-fullscreen leaflet-bar leaflet-control");
      btn.title = "展开全屏战术地图";
      btn.textContent = "⛶";
      btn.style.cssText = "display:flex;align-items:center;justify-content:center;font-size:18px;text-decoration:none;cursor:pointer;";
      L.DomEvent.on(btn, "click", (e) => {
        L.DomEvent.stop(e);
        if (document.fullscreenElement) void document.exitFullscreen();
        else void el.value?.requestFullscreen();
      });
      return btn;
    },
  });
  new Fullscreen().addTo(map);

  layerGroup = L.layerGroup().addTo(map);
  render();
  sizeObserver = new ResizeObserver(() => map?.invalidateSize({ pan: false }));
  sizeObserver.observe(el.value);
});

watch(() => [props.snap, props.selftest], render, { deep: true });
watch(visible, drawBattlefield);

onBeforeUnmount(() => { sizeObserver?.disconnect(); map?.remove(); map = null; });
</script>

<template>
  <div class="map-shell" :class="{ 'hide-unit-labels': !showUnitLabels }">
    <div v-if="!overview" class="map-toolbar" aria-label="战场地图图层">
      <button @click="toggleTerrainBase" :aria-pressed="terrainBase">{{ terrainBase ? '卫星底图' : '地形底图' }}</button>
      <label><input type="checkbox" v-model="showUnitLabels" />单位标注</label>
      <label><input type="checkbox" v-model="visible.terrain" />地形要素 {{ view?.terrain.features.length || '未记录' }}</label>
      <label><input type="checkbox" v-model="visible.sectors" />射界 {{ view?.fields_of_fire.length || '未记录' }}</label>
      <label><input type="checkbox" v-model="visible.ranges" />火力射程 {{ view?.fire_ranges.length ?? 0 }}</label>
      <label><input type="checkbox" v-model="visible.supply" />补给线 {{ view?.supply_lines.length || '未记录' }}</label>
      <span class="map-key">虚线：估计 · 红色补给线：受阻 · 射界未算遮挡</span>
    </div>
    <div ref="el" class="map-fill" role="region" :aria-label="overview ? '中国地图' : '战场态势地图'"></div>
  </div>
</template>

<style scoped>
.map-shell { width: 100%; height: 100%; display: flex; flex-direction: column; }
.map-fill { width: 100%; flex: 1; min-height: 180px; }
.map-toolbar { display: flex; align-items: center; flex-wrap: wrap; gap: 6px 14px; padding: 7px 12px; background: var(--panel); border-bottom: 1px solid var(--line); font-size: 12px; }
.map-toolbar button { padding: 4px 8px; font-size: 12px; }
.map-toolbar label { display: flex; align-items: center; gap: 4px; white-space: nowrap; cursor: pointer; }
.map-toolbar input { accent-color: var(--accent); }
.map-key { font-size: 11px; color: var(--muted); }
</style>

<style>
/* Folium DivIcon 等价：去掉 Leaflet 默认的白底边框 */
.nato-marker { background: transparent; border: none; }
.fire-marker { background: transparent; border: none; font-size: 20px; text-align: center; }
.trail-arrow { background: transparent; border: none; }
.supply-node { background: #222b38; color: #f0c777; border: 1px solid #f0c777; text-align: center; font-size: 17px; line-height: 20px; }

/* folium plugins.AntPath 的 CSS 动画等价：行军蚂蚁线 / 火力曳光线 */
.ant-march { stroke-dasharray: 15 20; animation: antdash 1.6s linear infinite; }
.ant-fire { stroke-dasharray: 5 20; animation: antdash 0.8s linear infinite; }
@keyframes antdash { to { stroke-dashoffset: -35; } }
@media (prefers-reduced-motion: reduce) { .ant-march, .ant-fire { animation: none; } }
</style>
