<script setup lang="ts">
import { computed } from "vue";
import type { BattlefieldView } from "../types";

const props = defineProps<{ view?: BattlefieldView }>();
const pct = (n: number | null | undefined) => n == null ? "未记录" : `${(n * 100).toFixed(1)}%`;
const count = (n: number | null | undefined) => n == null ? "—" : n.toLocaleString();
const delta = (n: number | null | undefined) => n == null ? '无对比记录' : `${n > 0 ? '+' : ''}${n.toLocaleString()}`;
const score = (n: number | null | undefined) => n == null ? '未记录' : n.toFixed(1);
const layerCount = (n: number) => n ? `${n} 条` : '未记录';
const equipment = computed(() => props.view?.losses.units.flatMap(u => u.equipment.map(e => ({ ...e, team: u.team, formation: u.name, status: u.status }))) ?? []);
const gaps = computed(() => props.view?.data_quality?.filter(row => row.status !== 'available').length ?? 0);
</script>

<template>
  <section class="battlefield-panel" data-testid="battlefield-panel">
    <template v-if="view">
      <div class="battlefield-heading">
        <h3>战场态势与损耗</h3>
        <span class="muted">已保存态势 · HP与人数分账</span>
      </div>
      <p class="terrain-summary">
        地形 <b>{{ view.terrain.label }}</b>
        <template v-if="view.terrain.factor != null"> · 机动拖慢 ×{{ view.terrain.factor }}（{{ view.terrain.source }}）</template>
        · 地形要素 {{ layerCount(view.terrain.features.length) }} · 当前火力范围 {{ view.fire_ranges.length }} 条
        · 射界 {{ layerCount(view.fields_of_fire.length) }} · 补给节点 {{ layerCount(view.supply_nodes.length) }} / 路线 {{ layerCount(view.supply_lines.length) }}
      </p>
      <details v-if="view.data_quality" class="battlefield-notes" data-testid="battlefield-data-quality">
        <summary>数据完整性 · {{ gaps ? `${gaps}项缺失或部分记录` : '各项已有记录' }} · 查看原因</summary>
        <div class="battlefield-table-scroll"><table>
          <thead><tr><th>项目</th><th>记录状态</th><th>口径 / 缺失原因</th></tr></thead>
          <tbody><tr v-for="row in view.data_quality" :key="row.key">
            <td>{{ row.label }}</td><td>{{ row.status === 'missing' ? '尚未提供' : row.total != null ? `${row.recorded}/${row.total}个单位` : `${row.recorded}条记录` }}</td>
            <td class="record-note">{{ row.note }}</td>
          </tr></tbody>
        </table></div>
      </details>
      <template v-if="view.operational">
        <div class="force-summaries" data-testid="operational-summary">
          <article v-for="team in view.operational.teams" :key="team.team" :class="['force-summary', team.team]">
            <div class="force-heading"><b>{{ team.team === 'red' ? '红方' : '蓝方' }}当前态势</b>
              <span class="muted">{{ team.active_units }} 个在编单位 · {{ team.disabled_units }} 个HP归零</span></div>
            <div class="force-values">
              <div><small>当前HP / 本轮变化</small><strong>{{ count(team.current_hp) }} / {{ delta(team.hp_change) }}</strong></div>
              <div><small>较首次记录HP下降</small><strong>{{ count(team.hp_decline) }} · {{ pct(team.hp_decline_rate) }}</strong></div>
              <div><small>平均士气 / 补给指数</small><strong>{{ score(team.mean_morale) }} / {{ score(team.mean_supply) }}</strong></div>
            </div>
            <p class="muted">HP对比覆盖 {{ team.comparable_units }}/{{ team.active_units }}，本轮对比覆盖 {{ team.changed_units }}/{{ team.active_units }}；均值按已有数值计算。</p>
          </article>
        </div>
        <p class="metric-note">{{ view.operational.note }}</p>
        <details class="battlefield-notes" open>
          <summary>单位状态与HP变化</summary>
          <div class="battlefield-table-scroll">
            <table data-testid="operational-units"><thead><tr><th>单位 / 方</th><th>首次记录HP</th><th>当前HP</th><th>本轮变化</th><th>HP下降比例</th><th>士气</th><th>补给指数</th><th>状态 / 对比起点</th></tr></thead>
              <tbody><tr v-for="u in view.operational.units" :key="u.team + u.name">
                <td>{{ u.name }} / {{ u.team === 'red' ? '红' : '蓝' }}</td><td>{{ count(u.baseline_hp) }}</td><td>{{ count(u.current_hp) }}</td>
                <td>{{ delta(u.hp_change) }}</td><td>{{ pct(u.hp_decline_rate) }}</td><td>{{ score(u.morale) }}</td><td>{{ score(u.supply) }}</td>
                <td class="record-note">{{ u.status }} · {{ u.baseline_time ?? '尚无已保存基线' }}</td>
              </tr></tbody>
            </table>
          </div>
        </details>
      </template>
      <h4>人员计数与装备台账</h4>
      <div class="force-summaries">
        <article v-for="team in view.losses.teams" :key="team.team" :class="['force-summary', team.team]">
          <div class="force-heading"><b>{{ team.team === 'red' ? '红方' : '蓝方' }}人员</b>
            <span class="muted">{{ team.recorded_units ? `计数完整 ${team.recorded_units}/${team.total_units} 个单位` : '模型尚未提供完整人数记录' }}</span></div>
          <template v-if="team.personnel">
            <div class="force-values">
              <div><small>当前编组可用 / 记录累计投入</small><strong>{{ count(team.active_current) }} / {{ count(team.personnel.committed) }}</strong></div>
              <div><small>战斗减员率</small><strong>{{ pct(team.personnel.loss_rate) }}</strong></div>
              <div><small>非战斗减员率</small><strong>{{ pct(team.personnel.secondary_rate) }}</strong></div>
            </div>
            <div class="readiness-track" role="img" :aria-label="`当前编组占累计投入 ${pct(team.active_current != null && team.personnel.committed ? team.active_current / team.personnel.committed : null)}`">
              <span v-if="team.active_current != null && team.personnel.committed" :style="{width: pct(Math.min(1, team.active_current / team.personnel.committed))}"></span>
            </div>
            <p v-if="team.archived_units" class="muted">退场 {{ team.archived_units }} 个单位；损失沿用最后记录，可用人数仅计当前编组。</p>
            <p v-if="team.personnel.note" class="metric-note">{{ team.personnel.note }}</p>
          </template>
          <p v-else class="metric-note">人数未知，不代表伤亡为0。已有HP变化显示在上方；补充结构化人数记录后才可计算人员战损率。</p>
        </article>
      </div>
      <h4>人员明细</h4>
      <div class="battlefield-table-scroll">
        <table>
          <thead><tr><th>单位</th><th>方</th><th>累计投入</th><th>现有</th><th>战斗减员</th><th>非战斗减员</th><th>战斗减员率</th><th>依据 / 状态</th></tr></thead>
          <tbody><tr v-for="u in view.losses.units" :key="u.team + u.name">
            <td>{{ u.name }}</td><td>{{ u.team === 'red' ? '红' : '蓝' }}</td>
            <td>{{ count(u.personnel?.committed) }}</td><td>{{ count(u.personnel?.current) }}</td>
            <td>{{ count(u.personnel?.casualties) }}</td><td>{{ count(u.personnel?.noncombat_losses) }}</td>
            <td>{{ pct(u.personnel?.loss_rate) }}</td>
            <td class="record-note">{{ u.personnel?.source ?? '未记录' }} · {{ u.status }}<span v-if="u.personnel?.note"> · {{ u.personnel.note }}</span></td>
          </tr></tbody>
        </table>
      </div>
      <h4>装备明细 <span class="muted">按类别与计量单位分账，待修与永久损失分开</span></h4>
      <div class="battlefield-table-scroll" v-if="equipment.length">
        <table>
          <thead><tr><th>单位 / 装备</th><th>方</th><th>累计投入</th><th>可用</th><th>待修</th><th>永久损失</th><th>永久损失率</th><th>可用率</th><th>依据</th></tr></thead>
          <tbody><tr v-for="(e, i) in equipment" :key="i">
            <td>{{ e.formation }} / {{ e.name }}（{{ e.unit }}）</td><td>{{ e.team === 'red' ? '红' : '蓝' }}</td>
            <td>{{ count(e.committed) }}</td><td>{{ count(e.operational) }}</td><td>{{ count(e.damaged) }}</td><td>{{ count(e.destroyed) }}</td>
            <td>{{ pct(e.loss_rate) }}</td><td>{{ pct(e.readiness_rate) }}</td><td class="record-note">{{ e.source }}<span v-if="e.note"> · {{ e.note }}</span></td>
          </tr></tbody>
        </table>
      </div>
      <p v-else class="muted">暂无装备数量记录，不能计算装备损失率。</p>
      <details class="battlefield-notes"><summary>图层与统计口径</summary>
        <p v-for="note in view.notes" :key="note" class="muted">{{ note }}</p>
        <p class="muted">汇总只包含计数完整的单位，退场单位保留最后记录；未知损失不按全损补齐。底图与地形标注供观察，未运行高程视线计算。</p>
      </details>
      <p v-for="note in view.warnings" :key="note" class="warnbox">{{ note }}</p>
    </template>
    <p v-else class="muted">当前后端尚未提供战场展示数据，请重启后端以加载新版。</p>
  </section>
</template>

<style scoped>
.battlefield-heading, .force-heading { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h3 { margin: 2px 0 8px; font-size: 16px; }
h4 { margin: 18px 0 8px; font-size: 13px; }
.terrain-summary { font-size: 12px; line-height: 1.8; color: var(--muted); }
.terrain-summary b { color: var(--text); }
.force-summaries { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.force-summary { border-top: 2px solid var(--line); padding: 10px 0; }
.force-summary.red { border-color: var(--bad); }
.force-summary.blue { border-color: var(--accent); }
.force-values { display: flex; gap: 20px; flex-wrap: wrap; margin-top: 10px; }
.force-values small { display: block; font-size: 11px; color: var(--muted); margin-bottom: 5px; }
.force-values strong { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
.readiness-track { height: 4px; background: var(--line); margin-top: 14px; }
.readiness-track span { display: block; height: 100%; background: var(--ok); }
.battlefield-table-scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 12px; text-align: left; font-variant-numeric: tabular-nums; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 500; }
td.record-note { white-space: normal; min-width: 150px; max-width: 280px; font-size: 11px; color: var(--muted); }
.battlefield-notes { margin-top: 16px; }
.metric-note { font-size: 12px; line-height: 1.7; color: var(--muted); padding: 9px 12px; border-left: 2px solid var(--accent); background: var(--panel); }
.battlefield-notes summary { cursor: pointer; font-size: 12px; }
@media (max-width: 900px) { .force-summaries { grid-template-columns: 1fr; gap: 4px; } }
</style>
