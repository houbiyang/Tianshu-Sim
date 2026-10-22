<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { api } from '../api';
import type { CausalAnalysis, CounterfactualResult, GeometryEffect, MetricContrast, Snapshot } from '../types';

const props = defineProps<{ sessionId: string; snap: Snapshot; busy: string }>();
const emit = defineEmits<{ busy: [label: string] }>();
const data = ref<CausalAnalysis | null>(null);
const error = ref('');
const localBusy = ref('');
const selectedTurn = ref(1);
const instruction = ref('');
const samples = ref(1);
const result = ref<CounterfactualResult | null>(null);
const verification = ref<Record<number, string>>({});
const turns = computed(() => data.value?.timeline.filter(t => t.turn > 0) ?? []);
const current = computed(() => data.value?.attributions.find(t => t.turn === Number(selectedTurn.value)));
const disabled = computed(() => !!props.busy || !!localBusy.value);

async function load() {
  if (!props.sessionId) return;
  error.value = '';
  try {
    data.value = await api.analysis(props.sessionId);
    selectedTurn.value = turns.value.at(-1)?.turn ?? 1;
  } catch (e) { error.value = String(e); }
}
watch(() => [props.sessionId, props.snap.turn_count, props.snap.causal?.latest_trace_id], load, { immediate: true });

async function operation(label: string, action: () => Promise<void>) {
  localBusy.value = label; error.value = ''; emit('busy', label);
  try { await action(); } catch (e) { error.value = String(e); }
  finally { localBusy.value = ''; emit('busy', ''); }
}
const explain = () => operation('正在核对轨迹并生成归因假说…', async () => { data.value = await api.explain(props.sessionId); });
const undo = (effect: GeometryEffect) => operation('正在重算撤销位移后的几何状态…', async () => {
  result.value = await api.counterfactual(props.sessionId, { turn: effect.turn, mode: 'geometry', undo: [{ team: effect.team, name: effect.name }] });
});
const branch = () => operation(`正在运行 ${samples.value} 组成对DSH分支…`, async () => {
  result.value = await api.counterfactual(props.sessionId, { turn: Number(selectedTurn.value), mode: 'model', instruction: instruction.value, samples: Number(samples.value) });
});
async function verify(turn: number) {
  try {
    const value = await api.trace(props.sessionId, turn);
    verification.value[turn] = value.persistence.length ? value.persistence.map(r => `${r.status}（${r.found_events ?? 0}/${r.expected_events ?? 0}个事件）`).join('；') : '没有可核验的DSH事件序号';
  } catch (e) { verification.value[turn] = String(e); }
}
const metricName = (key: string) => key.replace('red.', '红方 · ').replace('blue.', '蓝方 · ')
  .replace('retreat_blocked', '退路已断').replace('encircled', '被围').replace('contact', '机动可接触').replace('cut', '几何补给截断');
const fieldName = (key: string) => ({ can_contact: '机动可接触', supply: '几何补给', encirclement: '包围', retreat: '退路' } as Record<string, string>)[key] ?? key;
const valueName = (value: unknown) => typeof value === 'boolean' ? (value ? '是' : '否') : String(value ?? '未知');
const clockName = (status: string) => ({ verified: '已核验', unknown: '不可核验', mismatch: '不一致', initial: '开局' } as Record<string, string>)[status] ?? status;
const contrasts = computed(() => result.value?.metrics ? [result.value.metrics] : result.value?.samples?.map(s => s.metrics) ?? []);
const delta = (m: MetricContrast) => m.delta > 0 ? `+${m.delta}` : String(m.delta);
</script>

<template>
  <section class="causal-panel" data-testid="causal-panel">
    <div class="heading"><h3>关键决策、转折与反事实</h3><span class="muted">{{ snap.causal?.runtime.connected ? 'DSH原生插件已连接 · Native工具模式' : '运行时尚未连接或后端版本未更新' }} · 非PTC</span></div>
    <p class="scope">{{ data?.scope ?? '新开局后，每个已提交回合将保留决策前后快照和DSH执行事件。' }}</p>
    <p v-if="!snap.adjudication.enabled" class="muted">本局未开启确定性裁决；本页几何结果是独立只读试算，不代表正式回合由内核结算。</p>
    <p v-if="error" class="warnbox" role="alert">{{ error }}</p>
    <template v-if="data">
      <div class="toolbar"><label>分析回合 <select v-model="selectedTurn" :disabled="disabled || !turns.length"><option v-for="t in turns" :key="t.turn" :value="t.turn">T+{{ t.turn }}</option></select></label>
        <button @click="load" :disabled="disabled">刷新证据</button>
        <button @click="explain" :disabled="disabled || !turns.length">生成归因解读（1次模型调用）</button></div>
      <p v-if="!turns.length" class="muted">至少完成一轮推进后，才能比较决策前后变化；不会从旧战报补造执行轨迹。</p>

      <h4>走向转折候选</h4>
      <div v-for="point in data.turning_points" :key="point.trace_id" class="turning">
        <a :href="'#' + point.trace_id">T+{{ point.turn }}</a> · {{ point.status_changes.length }}项几何状态变化
        <p class="muted">{{ point.label }}</p>
        <ul><li v-for="(c, i) in point.status_changes.slice(0, 8)" :key="i">{{ c.name }} · {{ fieldName(c.field) }}：{{ valueName(c.factual) }} → {{ valueName(c.alternative) }}</li></ul>
      </div>
      <p v-if="!data.turning_points.length" class="muted">暂无几何状态转折证据；这不等于没有战略变化。</p>

      <h4>位移归因 · 固定其他终态逐项撤销</h4>
      <p class="muted">每回合最多重算12个位移，交互检查覆盖其中前4个有影响位移；影响项数不等于胜负贡献。</p>
      <div class="table-scroll"><table><thead><tr><th>单位</th><th>位移</th><th>撤销后变化项</th><th>反事实</th></tr></thead><tbody>
        <tr v-for="effect in current?.effects ?? []" :key="effect.team + effect.name"><td>{{ effect.name }}</td><td>{{ effect.moved_km }} km</td><td>{{ effect.status_change_count }}</td><td><button :disabled="disabled" @click="undo(effect)">撤销该位移试算</button></td></tr>
      </tbody></table></div>
      <p v-for="(pair, i) in current?.interactions.filter(p => p.nonadditive) ?? []" :key="i" class="muted">{{ pair.units.join(' + ') }}：联合撤销改变{{ pair.joint_status_changes }}项，单项合计{{ pair.single_counts_sum }}项，存在非加性。</p>

      <p v-if="snap.war_state?.mission" class="muted">本局采用固定命令规则，可在地图上方的目标面板对照红方待命决策；下方模型改写状态分支不适用。</p>
      <p v-if="snap.settings?.simulation_mode === 'replay'" class="muted">历史回放不用于反事实验收，请创建开放推演会话。</p>
      <h4>改变决策 · 成对模型重演</h4>
      <p class="muted">从选定决策之前的同一DSH事件前缀分出对照与干预两条原生子会话。每组调用模型2次，原战局保持不变；随机采样未受控，差异属于推演证据。</p>
      <label class="instruction">替代决策<textarea v-model="instruction" :disabled="disabled" maxlength="3000" placeholder="例如：红方先保持原位，延后一回合再实施侧翼机动；其他条件不变。"></textarea></label>
      <div class="toolbar"><label>重演组数 <select v-model="samples" :disabled="disabled"><option :value="1">1组</option><option :value="2">2组</option><option :value="3">3组</option></select></label>
        <button :disabled="disabled || !!snap.war_state?.mission || snap.settings?.simulation_mode === 'replay' || !turns.length || !instruction.trim()" @click="branch">运行成对反事实（{{ samples * 2 }}次调用）</button></div>
      <p v-if="localBusy" role="status">{{ localBusy }}</p>
      <article v-if="result" class="branch-result" data-testid="counterfactual-result"><b>{{ result.mode === 'controlled_geometry' ? '单步几何反事实' : '成对模型反事实' }} · T+{{ result.turn }} · {{ result.read_only ? '只读' : '状态未确认' }}</b><p>{{ result.scope }}</p>
        <ul v-if="result.status_changes?.length"><li v-for="(change, i) in result.status_changes.slice(0, 12)" :key="i">{{ change.name }} · {{ fieldName(change.field) }}：{{ valueName(change.factual) }} → {{ valueName(change.alternative) }}</li></ul>
        <div v-for="(rows, i) in contrasts" :key="i" class="table-scroll"><p v-if="result.samples">第{{ i + 1 }}组：{{ result.samples[i]?.admissible ? '通过位移、计数和钟点校验' : '样本校验未通过或时间不可核验，不作为有效因果样本' }}</p>
          <table><thead><tr><th>指标（单位数）</th><th>原态势 / 对照</th><th>干预</th><th>变化</th></tr></thead><tbody><tr v-for="m in rows" :key="m.metric"><td>{{ metricName(m.metric) }}</td><td>{{ m.factual }}</td><td>{{ m.alternative }}</td><td>{{ delta(m) }}</td></tr></tbody></table></div>
        <p v-for="note in result.limitations ?? []" :key="note" class="muted">{{ note }}</p>
      </article>

      <article v-if="data.explanation" class="explanation"><h4>模型归因假说 · 非因果定论</h4><p>{{ data.explanation.summary }}</p><div v-for="(item, i) in data.explanation.hypotheses" :key="i"><b>{{ item.claim }}</b><p>替代解释：{{ item.alternative }}</p><p>局限：{{ item.limitation }}</p><a v-for="id in item.evidence_ids" :key="id" :href="'#' + id">{{ id }} </a></div></article>

      <h4>决策与DSH执行轨迹</h4>
      <details v-for="trace in data.timeline" :key="trace.id" :id="trace.id" class="trace" data-testid="decision-trace">
        <summary>T+{{ trace.turn }} · {{ trace.decisions.length }}条模型决策声明 · {{ trace.runs.length }}次DSH调用 · 时间{{ clockName(trace.clock?.status) }}</summary>
        <p class="trace-id">{{ trace.id }}</p><p>{{ trace.report }}</p>
        <div v-for="decision in trace.decisions" :key="decision.id"><b>{{ decision.unit_name || decision.team }}：{{ decision.action }}</b><p>模型声明的依据：{{ decision.rationale }}</p><p>预期：{{ decision.expected_effect }}</p></div>
        <p v-if="!trace.decisions.length" class="muted">本回合没有可采纳的决策声明；只保留实际状态变化，不补写动机。</p>
        <div v-for="run in trace.runs" :key="run.id" class="run"><code>{{ run.session_id }}</code><p>事件 {{ run.first_seq ?? '未知' }}—{{ run.last_seq ?? '未知' }} · {{ run.finish_reason }} · {{ run.evidence }}</p>
          <div class="table-scroll"><table><thead><tr><th>序号</th><th>DSH事件</th><th>工具 / 请求策略</th></tr></thead><tbody><tr v-for="(event, i) in run.events" :key="i"><td>{{ event.seq ?? '—' }}</td><td>{{ event.type }}</td><td>{{ event.name ?? event.tools?.join(', ') ?? (event.request_config ? JSON.stringify(event.request_config) : '') }}</td></tr></tbody></table></div></div>
        <button @click="verify(trace.turn)" :disabled="disabled">核验DSH持久化日志</button><p v-if="verification[trace.turn]" role="status">{{ verification[trace.turn] }}</p>
      </details>
      <details><summary>已加载的观测与控制插件</summary><p v-for="plugin in snap.causal?.plugins ?? []" :key="plugin.name">{{ plugin.name }} · {{ plugin.hooks.join(' / ') }}</p><p v-for="limit in data.limits" :key="limit" class="muted">{{ limit }}</p></details>
    </template>
  </section>
</template>

<style scoped>
h3 { margin: 0; font-size: 16px; } h4 { margin: 24px 0 10px; font-size: 14px; }
.heading, .toolbar { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; } .heading { justify-content: space-between; }
.scope, p, li { font-size: 12px; line-height: 1.8; } .muted { color: var(--muted); }
.toolbar { margin: 12px 0; } label { font-size: 12px; } select { margin-left: 6px; }
button, select, textarea { color: var(--text); background: var(--panel); border: 1px solid var(--line); border-radius: 4px; padding: 7px 10px; font: inherit; }
button { cursor: pointer; } button:disabled { opacity: .5; cursor: default; } button:focus-visible, textarea:focus-visible, select:focus-visible { outline: 2px solid var(--accent); }
.instruction { display: block; } textarea { display: block; width: 100%; box-sizing: border-box; min-height: 80px; margin-top: 6px; resize: vertical; }
.table-scroll { overflow-x: auto; } table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; }
th, td { padding: 8px; border-bottom: 1px solid var(--line); } th { color: var(--muted); font-weight: 500; } td:first-child { min-width: 130px; }
.turning, .trace, .branch-result, .explanation { padding: 12px 0; border-top: 1px solid var(--line); } .turning { border-left: 2px solid var(--accent); padding-left: 12px; }
.trace summary { cursor: pointer; font-size: 12px; } .trace-id, code { overflow-wrap: anywhere; color: var(--muted); font-size: 11px; }
.run { margin: 12px 0; } a { color: var(--accent); overflow-wrap: anywhere; } .warnbox { color: var(--bad); }
@media(max-width: 700px) { .toolbar { align-items: stretch; } .toolbar button { flex: 1 1 160px; } }
</style>
