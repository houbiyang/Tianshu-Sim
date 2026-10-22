<script setup lang="ts">
import { ref, watch } from 'vue';
import { api } from '../api';
import type { MissionState, CounterfactualResult } from '../types';
const props = defineProps<{ mission: MissionState; sessionId: string; busy: boolean }>();
const pending = ref(false); const error = ref(''); const comparison = ref<CounterfactualResult | null>(null);
watch(() => [props.sessionId, props.mission.turn], () => { comparison.value = null; error.value = ''; });
async function compareHold() {
  pending.value = true; error.value = '';
  try {
    comparison.value = await api.counterfactual(props.sessionId, { turn: props.mission.turn, mode: 'mission',
      orders: props.mission.last_orders.filter(o => !o.unit.startsWith('红方')) });
  } catch (e) { error.value = String(e); }
  finally { pending.value = false; }
}
</script>
<template>
  <section class="mission-panel" aria-label="局部教学任务" data-testid="mission-panel">
    <div class="mission-heading"><strong>局部教学 · {{ mission.status === 'active' ? '进行中' : '已结算' }}</strong><span>{{ mission.turn }} / {{ mission.definition.deadline_turn }} 回合 · {{ mission.definition.time_step }}/轮</span></div>
    <p><b>目标：</b>{{ mission.definition.goal }} <span v-if="mission.reason"> · {{ mission.reason }}（{{ mission.winner === 'red' ? '红方' : '蓝方' }}）</span></p>
    <p class="muted">{{ mission.definition.assumptions }}。目标圈与出口是示意位置，地图背景不构成已核验的战术地形。</p>
    <details><summary>目标进度、补给账与重放凭据</summary>
      <p>连续满足 {{ mission.progress }} 轮 · 交通完好度 {{ mission.network }} · 组织存续 {{ mission.organization }}</p>
      <div class="table-scroll"><table><thead><tr><th>编组</th><th>行动</th><th>线路（回合开始）</th><th>到货</th><th>粮食余额</th><th>弹药余额</th></tr></thead><tbody><tr v-for="r in mission.ledger" :key="r.unit"><td>{{ r.unit }}</td><td>{{ ({ hold:'待命', move:'移动', fire:'射击', resupply:'补给', sabotage:'破袭', repair:'修复', conceal:'隐蔽', air_support:'空中支援', accept:'接收' } as Record<string,string>)[r.action] }}</td><td>{{ r.supply_status }}</td><td>{{ r.delivery }}</td><td>{{ r.food_after }}</td><td>{{ r.ammo_after }}</td></tr></tbody></table></div>
      <p class="muted">库存单位为教学工作量，非真实弹药发数；每份到货含一单位粮食和一单位弹药。</p>
      <p class="hash">规则 {{ mission.version }} · 定义 {{ mission.definition_hash }}<br>初态 {{ mission.initial_hash }}</p>
      <button :disabled="busy || pending || mission.turn < 1" @click="compareHold">{{ pending ? '正在重算…' : '对照上一轮：红方全部原地待命' }}</button>
      <p class="muted">保持蓝方命令和决策前态势不变，只替换红方命令；对照结果不写回战局。</p>
      <p v-if="error" class="bad" role="alert">{{ error }}</p>
      <div v-if="comparison" class="table-scroll"><table><thead><tr><th>指标</th><th>原决策</th><th>待命对照</th><th>差值</th></tr></thead><tbody><tr v-for="m in comparison.metrics" :key="m.metric"><td>{{ m.metric }}</td><td>{{ m.factual }}</td><td>{{ m.alternative }}</td><td>{{ m.delta }}</td></tr></tbody></table></div>
    </details>
  </section>
</template>
<style scoped>
.mission-panel { flex: 0 0 auto; margin: 8px 12px; padding: 12px 16px; border: 1px solid #57655e; border-left: 3px solid #c9b47d; border-radius: 6px; background: #192831; font-size: 13px; max-height: 32vh; overflow: auto; }
.mission-heading { position: sticky; top: -12px; z-index: 1; background: #192831; padding: 8px 0; display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
p { margin: 7px 0; line-height: 1.5; } summary { cursor: pointer; } .hash { overflow-wrap: anywhere; font-size: 11px; color: #a6b6c2; }
.table-scroll { overflow-x: auto; } table { width: 100%; border-collapse: collapse; white-space: nowrap; } td, th { padding: 6px 10px; text-align: left; border-bottom: 1px solid #344651; }
@media(max-width: 700px) { .mission-panel { margin: 6px; padding: 10px; max-height: 40vh; } }
</style>
