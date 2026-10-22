<script setup lang="ts">
// ⚖️ 裁决面板：app.py 地图下方裁决区的完整移植 —— 结论表、汇总统计、
// 几何尺度明细、位移越界明细、裁决口径说明，一个都不能少。
import { computed, ref } from "vue";
import type { Snapshot } from "../types";

const props = defineProps<{ snap: Snapshot }>();

const adj = computed(() => props.snap.adjudication);
const last = computed(() => adj.value.last);
const spec = computed(() => adj.value.spec);
const summary = computed(() => last.value?.summary ?? {});

const supplyClass = (v: string) => v === "已切断" ? "bad" : v === "无后方" ? "warn" : "ok";
const encircleClass = (v: string) => v === "被围" ? "bad" : v === "半包围" ? "warn" : "ok";
const retreatClass = (v: string) => v === "已断" ? "bad" : "ok";

const reachRange = computed(() => {
  if (!last.value || last.value.units.length === 0) return null;
  const reaches = last.value.units.map((u) => u.reach_km);
  return [Math.min(...reaches), Math.max(...reaches)] as const;
});
const showNoContact = computed(() =>
  (summary.value.units ?? 0) >= 2 && (summary.value.can_contact ?? 0) === 0 && reachRange.value);

const scaleOpen = ref(false);
const rulesOpen = ref(false);
</script>

<template>
  <section class="panel" v-if="adj.enabled && last">
    <h3>⚖️ 确定性裁决 <code>{{ last.version }}</code> · 指纹 <code>{{ last.fingerprint }}</code></h3>

    <template v-if="spec">
      <p class="spec-line">参数：{{ spec.summary }}
        <span v-if="!spec.calibrated" class="warn">（未配置，使用默认几何）</span></p>
      <p v-for="w in spec.warnings" class="muted">ℹ️ {{ w }}</p>
      <p v-if="spec.errors.length" class="bad">裁决参数有误（出错项已忽略，其余照常生效）：{{ spec.errors.join("；") }}</p>
    </template>

    <p class="summary">
      单位 <b>{{ summary.units }}</b> · 本回合可接触 <b>{{ summary.can_contact }}</b> ·
      粮道被断 <b>{{ summary.supply_cut }}</b> · 被围 <b>{{ summary.encircled }}</b>（半包围 {{ summary.partial }}）·
      退路已断 <b>{{ summary.retreat_cut }}</b> ·
      士气崩溃 <b>{{ summary.morale_broken }}</b> / 动摇 {{ summary.morale_shaken }}
    </p>

    <p v-if="showNoContact" class="muted">
      ℹ️ 本回合没有任何单位能接战：跨度 <code>{{ last.time_step }}</code> 下，各单位最多走
      <b>{{ reachRange![0].toFixed(1) }}–{{ reachRange![1].toFixed(1) }} km</b>（地形 ×{{ last.terrain_factor }}）。
      接触还包含 {{ last.contact_km }} km 的接触带宽；它不代表武器射程或命中。
      这是几何结论，不是渲染问题 —— 想看到接战，请调大时间跨度，或让部队初始距离更近。
    </p>

    <table>
      <thead>
        <tr><th>单位</th><th>方</th><th>兵种</th><th>可机动(km)</th><th>最近敌军(km)</th><th>补给</th><th>包围</th><th>退路</th><th>士气</th></tr>
      </thead>
      <tbody>
        <tr v-for="u in last.units" :key="u.team + u.name">
          <td>{{ u.name }}</td>
          <td :class="u.team">{{ u.team === "red" ? "红" : "蓝" }}</td>
          <td>{{ u.type || "—" }}</td>
          <td>{{ u.reach_km }}</td>
          <td>{{ u.nearest_enemy ? `${u.nearest_enemy} ${u.nearest_enemy_km?.toFixed(1)}` : "—" }}</td>
          <td :class="supplyClass(u.supply)">{{ u.supply }}</td>
          <td :class="encircleClass(u.encirclement)">{{ u.encirclement }}</td>
          <td :class="retreatClass(u.retreat)">{{ u.retreat }}</td>
          <td>{{ u.morale_state }}</td>
        </tr>
      </tbody>
    </table>

    <div class="expander">
      <button class="link" @click="scaleOpen = !scaleOpen">📐 本局几何尺度明细 {{ scaleOpen ? "▾" : "▸" }}</button>
      <template v-if="scaleOpen">
        <table v-if="spec?.table">
          <thead><tr><th>参数</th><th>值</th><th>来源</th><th>含义</th></tr></thead>
          <tbody>
            <tr v-for="row in spec.table as any[]" :key="row['参数']">
              <td>{{ row["参数"] }}</td><td>{{ row["值"] }}</td><td>{{ row["来源"] }}</td><td class="muted">{{ row["含义"] }}</td>
            </tr>
          </tbody>
        </table>
        <p class="muted" v-if="spec?.source">来源：<code>{{ spec.source }}</code></p>
        <p class="muted" v-else>来源：默认几何（adjudication/ 下没有这场战役的文件）</p>
      </template>
    </div>

    <div v-if="adj.violations.length" class="violations">
      <p class="bad">⚠️ 上回合有 <b>{{ adj.violations.length }}</b> 个单位的位移超出裁决上限：
        <span v-for="(v, i) in adj.violations" :key="v.name">{{ v.name }} 实走 {{ v.moved_km?.toFixed(1) }} km（上限 {{ v.allowed_km?.toFixed(1) }} km，超 {{ v.over_km?.toFixed(1) }} km）{{ i < adj.violations.length - 1 ? "；" : "" }}</span>。
        裁决器不修正坐标（硬拉可能把部队拖进河里），只如实记录。</p>
    </div>

    <div class="expander">
      <button class="link" @click="rulesOpen = !rulesOpen">⚖️ 裁决口径（怎么算的） {{ rulesOpen ? "▾" : "▸" }}</button>
      <ul v-if="rulesOpen" class="rules">
        <li><b>机动上限</b> = 兵种战术速度 × 本回合时长 ÷ 地形系数（当前地形「{{ last.terrain || "未标注" }}」×{{ last.terrain_factor }}，来源：{{ last.terrain_source || "模型地形标签" }}{{ last.terrain_source === "战役参数库配置" ? "——战役文件已写下 terrain_factor，模型自报标签被覆盖" : "" }}）</li>
        <li><b>接触判定</b>：最近敌距 ≤ 可机动距离＋{{ last.contact_km }} km；接触不等于能直接射击。</li>
        <li><b>区域控制圈</b> = max({{ last.control_radius_km }} km, 攻击射程 × 1.2)；间瞄火力与保障分队依兵种规则不控制土地。</li>
        <li><b>补给源</b>优先使用单位绑定的补给点及明确线路；无配置才回退到己方后方单位。线路穿敌控区即判「已切断」</li>
        <li><b>包围</b> = 以单位为中心 24 个方向，被敌方控制圈封锁 ≥85% 判「被围」，≥50% 判「半包围」</li>
        <li><b>退路</b> = 背离敌军的 ±75° 扇区内，是否存在一条能跑出的通道</li>
        <li><b>可复现</b> = 同一战局 + 同一回合（当前第 {{ last.turn }} 回合）+ 同一跨度，指纹必然相同</li>
      </ul>
      <p v-if="rulesOpen" class="muted">
        几何层不裁决伤亡数字、士气具体分值、天气或指挥失误。固定局部教学模式另按显式系数计算战损和库存；普通模式仍由模型估计。
        裁决器刻意只碰「位置、时间、连通性」这三类可以复算的东西。
      </p>
    </div>
  </section>
  <section class="panel" v-else-if="adj.enabled && !last">
    <h3>⚖️ 裁决内核已开启，但本回合没有裁决结果</h3>
    <p class="muted">可能原因：内核运行失败（见顶部警告），或战局数据缺少可用坐标。</p>
  </section>
  <section class="panel" v-else>
    <h3>⚖️ 裁决内核关闭</h3>
    <p class="muted">已关闭：模型自行推演走位与胜负，<b>同一想定两次运行结果不同</b>。
      打开侧栏「启用确定性裁决」后可复现、可对照。</p>
  </section>
</template>

<style scoped>
table { width: 100%; border-collapse: collapse; font-size: 13px; margin: 8px 0; }
th, td { padding: 4px 8px; border-bottom: 1px solid var(--line); text-align: left; }
td.red { color: #e0483e; } td.blue { color: #6ea0ff; }
.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
.muted { color: var(--muted); font-size: 12px; }
code { background: #0d1117; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
.spec-line { font-size: 13px; margin: 4px 0; }
.summary { font-size: 13px; margin: 8px 0; }
.summary b { color: #fff; }
.expander { margin: 10px 0; }
button.link { background: none; border: none; color: var(--accent); padding: 0; cursor: pointer; font: inherit; }
.rules li { font-size: 13px; line-height: 1.7; }
.violations { margin-top: 10px; font-size: 13px; }
h3 code { margin: 0 4px; }
</style>
