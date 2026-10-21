<script setup lang="ts">
// 薄 UI 编排：与 app.py 的侧栏/地图/战报三区结构对齐，
// 所有「数据实况」caption（交战统计、史料注入、守恒提示）照搬旧版文案口径。
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from "vue";
import { api } from "./api";
import type { Battle, Health, NumericFlag, Snapshot } from "./types";
import MapView from "./components/MapView.vue";
import BattlefieldPanel from "./components/BattlefieldPanel.vue";
import AdjudicationPanel from "./components/AdjudicationPanel.vue";
import CausalPanel from "./components/CausalPanel.vue";
import LlmSettings from "./components/LlmSettings.vue";
import LaunchScreen from './components/LaunchScreen.vue';
import { RENDERER_VERSION, summarizeEngagements } from "./tactical";
import { mdToHtml } from "./markdown";

const TIME_STEPS = ["5分钟", "15分钟", "30分钟", "1小时", "6小时", "12小时", "1天", "1周", "1个月"];

const health = ref<Health | null>(null);
const battles = ref<Battle[]>([]);
const corpus = ref<{ files: number; chunks: number; chars: number; dir: string } | null>(null);
const snap = ref<Snapshot | null>(null);
const sessionId = ref<string>("");
const recentSessions = ref<{ session_id: string; turn_count: number; scenario: string }[]>([]);
const screen = ref<'launch' | 'workspace'>('launch');
const settingsDialog = ref<HTMLDialogElement | null>(null);
const settingsPanel = ref<InstanceType<typeof LlmSettings> | null>(null);
const dockCollapsed = ref(false);
let unsubscribeMenu: (() => void) | undefined;
function openSettings() { settingsDialog.value?.showModal(); void settingsPanel.value?.reload(); }
async function resumeSession(id: string) {
  if (await run('正在恢复战局…', () => api.get(id))) {
    if (snap.value) restoreOptions(snap.value);
    sessionId.value = id; localStorage.setItem('gentactix-session', id); screen.value = 'workspace';
  }
}
function startScenario(value: { scenario: string; battle: string; time_step: string; use_rag: boolean; use_adjudicator: boolean }) {
  Object.assign(opts, value); void init();
}
onBeforeUnmount(() => unsubscribeMenu?.());

const opts = reactive({
  scenario: "长平之战，前262年，秦赵上党对峙",
  time_step: "30分钟",
  use_adjudicator: false,
  battle: "" as string, // "" = 自动匹配
  use_rag: false, // 与旧 Streamlit 侧栏同默认：默认关闭，开启才受史料约束
  use_live_intel: false,
  use_agent_tools: false, // 智能体回合：默认关，开启后每回合多次模型往返
  persist_roles: true,
  agent_mode: 'native',
});

const selftest = ref(false);
const ragOpen = ref(false);

// 战役文件里的 time_step 提示：选中某场战役即预填建议回合跨度
const battleHint = computed(() => battles.value.find((b) => b.name === opts.battle)?.time_step ?? "");
function applyBattleHint() {
  const hint = battleHint.value;
  if (hint && TIME_STEPS.includes(hint)) opts.time_step = hint;
}

const busy = ref("");
const progress = ref<{ turns?: number; steps?: number; tokens?: number }>({});
watch(busy, (label, _old, cleanup) => {
  progress.value = {};
  if (!label || !sessionId.value) return;
  let active = true;
  const poll = async () => { try { const data = await api.progress(sessionId.value); if (active) progress.value = data.progress; } catch { /* Older backend has no progress endpoint. */ } };
  void poll();
  const timer = setInterval(poll, 1000);
  cleanup(() => { active = false; clearInterval(timer); });
});
const error = ref("");
const notices = ref<string[]>([]);
const tab = ref<"reports" | "battlefield" | "adjudication" | "causal" | "review" | "chat">("reports");
const chatInput = ref("");

onMounted(async () => {
  unsubscribeMenu = window.gentactix?.onMenuAction?.((action) => {
    if (action === 'open-llm-settings') openSettings();
    if (action === 'new-scenario' && !busy.value) screen.value = 'launch';
  });
  for (let attempt = 0; attempt < 3; attempt++) {
    if (await refreshBackend()) break;
    await new Promise(resolve => setTimeout(resolve, 1200));
  }
});
async function refreshBackend() {
  error.value = '';
  try {
    health.value = await api.health();
    if (!health.value.runtime?.enabled) opts.agent_mode = 'json';
    battles.value = (await api.battles()).battles;
    corpus.value = await api.corpus(); // 开局前也要显示「已索引 N 文件」，与旧侧栏一致
    try { recentSessions.value = (await api.sessions()).sessions.filter(s => s.initialized); }
    catch { recentSessions.value = []; }
    const saved = localStorage.getItem('gentactix-session');
    if (saved) {
      try { const savedState = await api.get(saved); apply(savedState); restoreOptions(savedState); sessionId.value = saved; }
      catch { localStorage.removeItem('gentactix-session'); }
    }
    return true;
  } catch (e) {
    error.value = `后端未就绪：${e}`;
    return false;
  }
}

function restoreOptions(s: Snapshot) {
  Object.assign(opts, { scenario: s.scenario, time_step: s.time_step,
    battle: s.adjudication.battle_choice || '', use_adjudicator: s.adjudication.enabled,
    use_rag: s.rag.enabled, ...s.settings });
}

function apply(s: Snapshot) {
  snap.value = s;
  if (s.war_state) recentSessions.value = [{ session_id: s.session_id, turn_count: s.turn_count, scenario: s.scenario },
    ...recentSessions.value.filter(item => item.session_id !== s.session_id)];
  notices.value = s.notices.map((n) => n.message);
  if (notices.value.length) setTimeout(() => (notices.value = []), 12000);
}

async function run(label: string, fn: () => Promise<Snapshot>) {
  busy.value = label;
  error.value = "";
  try {
    apply(await fn());
    return true;
  } catch (e) {
    error.value = String(e);
    return false;
  } finally {
    busy.value = "";
  }
}

async function init() {
  error.value = "";
  if (!opts.scenario.trim()) { error.value = "请先输入推演场景"; return; }
  if (opts.use_agent_tools && opts.agent_mode === 'native' && !health.value?.runtime?.enabled) {
    error.value = '后端未启用原生运行时插件，请重启新版后端或选择兼容JSON模式'; return;
  }
  busy.value = "创建会话…";
  const previousSession = sessionId.value;
  try {
    const { session_id } = await api.createSession({
      scenario: opts.scenario,
      time_step: opts.time_step,
      use_adjudicator: opts.use_adjudicator,
      battle: opts.battle || null,
      use_rag: opts.use_rag,
      use_live_intel: opts.use_live_intel,
      use_agent_tools: opts.use_agent_tools,
      persist_roles: opts.persist_roles,
      agent_mode: opts.agent_mode,
    });
    sessionId.value = session_id;
  } catch (e) {
    busy.value = "";
    error.value = String(e);
    return;
  }
  const succeeded = await run("正在侦察地形并部署单位…", () => api.init(sessionId.value));
  if (succeeded) {
    localStorage.setItem('gentactix-session', sessionId.value);
    screen.value = 'workspace';
  } else sessionId.value = previousSession;
}

const next = () => run(`正在推演未来 ${snap.value?.time_step ?? ""} 的战况…`, () => api.next(sessionId.value));
const review = () => run("参谋长正在深度复盘…", () => api.review(sessionId.value));

async function cancel() {
  try { const response = await api.cancel(sessionId.value); notices.value = [response.note]; }
  catch (e) { error.value = String(e); }
}

async function sendChat() {
  const q = chatInput.value.trim();
  if (!q || !sessionId.value) return;
  chatInput.value = "";
  const previousMessages = snap.value?.chat_messages.slice() ?? [];
  if (snap.value) snap.value.chat_messages.push({ role: "user", content: q });
  if (!await run("璇玑思考中…", () => api.chat(sessionId.value, q))) {
    if (snap.value) snap.value.chat_messages = previousMessages;
    chatInput.value = q;
  }
}

async function exportJson() {
  if (!sessionId.value) return;
  try {
    const value = await api.exportSession(sessionId.value);
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = `GenTactix_T${value.turn_count}.json`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { error.value = `导出失败：${e}`; }
}

const hasState = () => !!snap.value?.war_state;

// ---- 地图下方「数据实况」：与 app.py 的 caption 同一口径 ----
const engStats = computed(() => snap.value?.war_state ? summarizeEngagements(snap.value.war_state) : null);
const engCaption = computed(() => {
  const e = engStats.value;
  if (!e) return "";
  if (e.total === 0) {
    return `⚔️ 本回合交战：0 处 —— AI 未输出 engagements（双方尚未接触），所以地图上没有扇区可画，` +
      `不是渲染失效。可展开侧栏「🔧 渲染自检」一键验证（当前渲染器 ${RENDERER_VERSION}）。`;
  }
  const parts = [`定向扇区 ${e.sector} 处`];
  if (e.ring) parts.push(`降级空心环 ${e.ring} 处（AI 未给攻击方位）`);
  let note = "";
  if (e.skipped) {
    const unique = [...new Set(e.reasons)].join("、");
    note = ` · ⚠️ 另有 ${e.skipped} 处被丢弃：${unique}`;
  }
  return `⚔️ 本回合交战：${e.valid} 处（${parts.join("；")}）${note}`;
});

const ragLast = computed(() => snap.value?.rag.last ?? null);
const ragMapCaption = computed(() => {
  const m = ragLast.value;
  if (!m?.enabled) return "";
  if (m.injected) {
    const first = m.hits[0];
    return `📚 本回合注入史料 ${m.injected} 段 · 主要来源：${first?.doc_title ?? ""}·${first?.section ?? ""}`;
  }
  return `📚 本回合无史料注入：${m.note} —— 数字与时间线不可追溯，仅作推演参考。`;
});

// ---- 裁决实况（旧版地图下方的汇总 caption + 越界警告，一眼可扫） ----
const adjLast = computed(() => snap.value?.adjudication.last ?? null);
const violations = computed(() => snap.value?.adjudication.violations ?? []);
const adjCaption = computed(() => {
  const a = adjLast.value;
  if (!a) return "";
  const s = a.summary;
  return `⚖️ 裁决 ${a.version} · 指纹 ${a.fingerprint}：单位 ${s.units} · 本回合可接触 ${s.can_contact} · ` +
    `粮道被断 ${s.supply_cut} · 被围 ${s.encircled}（半包围 ${s.partial}）· 退路已断 ${s.retreat_cut} · ` +
    `士气崩溃 ${s.morale_broken} / 动摇 ${s.morale_shaken}`;
});

// ---- 守恒面板：名单回填 / 歼灭 / 可疑 / 数值异常（app.py 同款文案） ----
const roster = computed(() => snap.value?.integrity.roster ?? null);
const numericFlags = computed(() => snap.value?.integrity.numeric_flags ?? []);
function flagLine(f: NumericFlag): string {
  if (f.kind === "hp_gain") return `${f.name} hp ${f.prev} → ${f.new}（+${f.delta}）`;
  if (f.kind === "hp_zero_alive") return `${f.name} hp 已归零但仍在编制里（地图会留一个满编军标）`;
  return `${f.name} hp 为负（${f.new}）`;
}
</script>

<template>
  <div class="desktop-root">
  <LaunchScreen v-if="screen === 'launch'" :battles="battles" :health="health" :busy="busy" :error="error" :can-resume="hasState()" :recent-sessions="recentSessions" @settings="openSettings" @resume="screen = 'workspace'" @start="startScenario" @reconnect="refreshBackend" @resume-session="resumeSession" @cancel="cancel" />
  <template v-else>
  <header class="workspace-command-bar">
    <div class="product-mark"><img :src="'./icons/gentactix.png'" alt=""><strong>GenTactix <span>DSH</span></strong></div>
    <nav aria-label="工作台菜单"><button @click="screen = 'launch'" :disabled="!!busy">想定管理</button><button @click="openSettings">模型配置</button><button @click="exportJson" :disabled="!hasState()">导出战局</button></nav>
    <span class="workspace-session">{{ snap?.scenario }}</span>
  </header>
  <div class="shell">
    <!-- 侧栏 -->
    <aside class="sidebar">
      <h1>想定与控制</h1>
      <p class="sub">SCENARIO & CONTROL</p>

      <div class="health" v-if="health">
        <span class="dot" :class="health.harness_started ? 'on' : 'idle'"></span>
        {{ health.engine }} · {{ health.model }}<br/>
        <small>{{ health.base_url }}</small>
      </div>
      <div class="health bad" v-else>后端未连接</div>
      <p v-if="health?.build?.restart_required" class="bad" role="alert">后端源码已更新，当前进程需要重启；请先保存战局。</p>

      <label>推演场景</label>
      <textarea v-model="opts.scenario" rows="3" :disabled="!!busy"></textarea>

      <label>⏱️ 单回合时间跨度</label>
      <select v-model="opts.time_step" :disabled="!!busy">
        <option v-for="t in TIME_STEPS" :key="t">{{ t }}</option>
      </select>

      <label class="toggle"><input type="checkbox" v-model="opts.use_adjudicator" :disabled="!!busy" />
        ⚖️ 启用确定性裁决（几何层，默认关闭）</label>
        <p v-if="opts.use_adjudicator" class="muted">内核 <code>{{ health?.adjudicator_version ?? "未知" }}</code> · 只裁决<b>机动 / 补给 / 包围</b>，不裁决伤亡数字。</p>
        <label>🎯 战场裁决库</label>
        <select v-model="opts.battle" :disabled="!!busy" @change="applyBattleHint">
          <option value="">自动匹配（按场景描述）</option>
          <option v-for="b in battles" :key="b.name" :value="b.name">{{ b.name }}</option>
        </select>
        <p class="muted" v-if="battles.length === 0">裁决库为空：adjudication/ 下还没有战役文件，将使用默认几何。</p>
        <p class="muted" v-else-if="battleHint">这场战役建议回合跨度 <b>{{ battleHint }}</b>（已预填；改小则每回合部队挪不动、永远打不着）。</p>
      <p class="muted" v-if="!opts.use_adjudicator">裁决已关闭：模型自行推演走位与胜负，<b>同一想定两次运行结果不同</b>。</p>

      <label class="toggle"><input type="checkbox" v-model="opts.use_agent_tools" :disabled="!!busy" />
        🤖 智能体回合（先侦察试算、后下令，默认关闭）</label>
      <p class="muted" v-if="opts.use_agent_tools">
        原生模式使用运行时步骤与工具预算；兼容模式至多4次JSON往返。<b>耗时显著增加</b>；
        工具全部只读，战局仍由系统统一提交。</p>
      <select v-if="opts.use_agent_tools" v-model="opts.agent_mode" :disabled="!!busy" aria-label="智能体工具模式"><option value="native" :disabled="!health?.runtime?.enabled">DSH原生领域工具</option><option value="json">兼容JSON协议</option></select>
      <p v-if="opts.use_agent_tools && !health?.runtime?.enabled" class="muted">原生插件未加载，当前使用兼容JSON协议。</p>
      <label class="toggle"><input type="checkbox" v-model="opts.persist_roles" :disabled="!!busy" />连续角色记忆（决策轨迹始终记录）</label>

      <label class="toggle"><input type="checkbox" v-model="opts.use_rag" :disabled="!!busy" />
        📚 启用史料检索（RAG）</label>
      <p class="muted" v-if="!opts.use_rag">未开启：模型凭自身历史知识推演，<b>数字与时间线不可追溯</b>。</p>
      <p class="bad" v-else-if="corpus && corpus.chunks === 0">
        ⚠️ 史料库为空（{{ corpus.dir }}）。本次推演仍由模型自由发挥，<b>没有史料约束</b>。</p>
      <p class="muted" v-else-if="corpus">
        已索引 {{ corpus.files }} 个文件 / {{ corpus.chunks }} 个片段（{{ corpus.chars.toLocaleString() }} 字）
      </p>

      <div class="expander" v-if="opts.use_rag && ragLast">
        <button class="link" @click="ragOpen = !ragOpen">
          📚 最近一次注入（{{ ragLast.injected }}/{{ ragLast.hits.length }} 段）{{ ragOpen ? "▾" : "▸" }}</button>
        <template v-if="ragOpen">
          <p class="muted" v-if="ragLast.note">{{ ragLast.note }}</p>
          <p class="muted" v-if="ragLast.hits.length === 0">查询：<code>{{ ragLast.query.slice(0, 60) }}</code></p>
          <div v-for="(h, i) in ragLast.hits" :key="i" class="hit">
            <b>{{ h.section }}</b> <code>{{ h.source }}</code>
            <p class="muted">{{ h.preview }}</p>
          </div>
          <p class="muted">若这里的内容与战报不符，说明模型没有遵守史料约束，应以史料为准。</p>
        </template>
      </div>

      <div class="expander">
        <label class="toggle"><input type="checkbox" v-model="selftest" :disabled="!!busy" />
          🔧 叠加 1 个示例交战扇区</label>
        <p class="muted">只在地图渲染时临时加一个虚构交战，不写入战局数据，关掉即消失。
          用来区分「渲染失效」和「本回合确实没交战」。</p>
        <p class="muted">渲染器版本：<code>{{ RENDERER_VERSION }}</code> · 交战图形：定向扇区；AI 未给攻击方位时降级为空心虚线环。</p>
      </div>

      <div class="buttons">
        <button v-if="busy && sessionId" class="cancel" @click="cancel">取消当前操作</button>
        <button class="primary" @click="init" :disabled="!!busy">🚀 初始化沙盘</button>
        <button @click="next" :disabled="!!busy || !hasState()">⏩ 下一轮推演</button>
        <button @click="review" :disabled="!!busy || !hasState()">🧐 呼叫总参谋长复盘</button>
        <button @click="exportJson" :disabled="!hasState()">📦 一键导出战局快照 (JSON)</button>
      </div>

      <div class="busy" v-if="busy" role="status">⏳ {{ busy }}<span v-if="progress.steps"> · DSH {{ progress.turns }}轮 / {{ progress.steps }}步 / {{ progress.tokens ?? 0 }} tokens</span></div>
      <div class="error" v-if="error">❌ {{ error }}</div>
    </aside>

    <!-- 主区 -->
    <main class="main" :class="{ 'dock-collapsed': dockCollapsed }">
      <header v-if="hasState()">
        <span>🕒 当前战区时间：<b>{{ snap!.war_state!.current_time ?? "—" }}</b></span>
        <span>第 {{ snap!.turn_count }} 回合</span>
        <span class="muted">{{ snap!.scenario }}</span>
      </header>

      <div class="map-wrap" v-if="hasState()"><MapView :snap="snap" :selftest="selftest" /></div>
      <div class="idle" v-else>
        <img :src="'./logo.png'" alt="GenTactix" class="idle-logo" @error="($event.target as HTMLImageElement).style.display='none'" />
        <h3>系统处于最高物理静默状态</h3>
        <p>👈 统帅，请配置推演场景，并点击 [🚀 初始化沙盘] 唤醒系统。</p>
      </div>

      <!-- 数据实况条：交战统计 / 史料注入 / 守恒提示，眼睛往下扫一眼就有答案 -->
      <div class="strip" v-if="hasState()">
        <p class="caption">{{ engCaption }}</p>
        <p class="caption" v-if="ragMapCaption">{{ ragMapCaption }}</p>
        <p class="caption" v-if="adjCaption">{{ adjCaption }}</p>
        <p class="caption" v-if="snap?.engine_meta">
          🔎 {{ snap.engine_meta.label }} · finish_reason <code>{{ snap.engine_meta.finish_reason ?? "未知" }}</code>
          · {{ snap.engine_meta.turns }} 轮 / {{ snap.engine_meta.steps }} 步
          · {{ snap.engine_meta.tokens }} tok · 用时 {{ (snap.engine_meta.elapsed_ms / 1000).toFixed(1) }}s
        </p>
        <p class="warnbox" v-if="snap && snap.adjudication.enabled && violations.length">
          ⚠️ 上回合有 <b>{{ violations.length }}</b> 个单位的位移超出裁决上限：
          {{ violations.map((v) => `${v.name} 实走 ${v.moved_km?.toFixed(1)} km（上限 ${v.allowed_km?.toFixed(1)} km，超 ${v.over_km?.toFixed(1)} km）`).join("；") }}。
          裁决器不修正坐标（硬拉可能把部队拖进河里），只如实记录。
        </p>

        <p class="warnbox" v-if="roster && (roster.recovered.length || roster.structure_missing?.length)">
          🔁 上回合有 <b>{{ roster.recovered.length }}</b> 个单位的返回里凭空消失，已按上一回合快照回填：
          {{ roster.recovered.map((e) => `${e.name}（hp ${e.hp}）`).join("、") || "无" }}。
          <template v-if="roster.structure_missing?.length">结构层面：{{ roster.structure_missing.join("；") }}。</template>
          战局状态是<b>整体替换</b>的，漏返回的单位下一回合连名字都不会出现在 prompt 里 ——
          它永远补不回来，所以这里必须补。若这支部队确实已被歼灭，应写进 eliminated 声明；没有声明的消失一律当作漏返回。
        </p>
        <p class="caption" v-if="roster?.eliminated?.length">
          ☠️ 本回合确认歼灭 <b>{{ roster.eliminated.length }}</b> 个：
          {{ roster.eliminated.map((e) => `${e.name}（${e.why}）`).join("、") }}
        </p>
        <p class="caption" v-if="roster?.suspected?.length">
          ☑️ 这些单位的消失已被回填，但上一回合 hp 已经很低，也可能确实被歼灭：
          {{ roster.suspected.map((e) => `${e.name}（上回合 hp ${e.hp}）`).join("、") }}。
          下一回合若确认歼灭，请让模型写进 eliminated。
        </p>
        <p class="warnbox" v-if="numericFlags.length">
          📉 数值守恒提示：{{ numericFlags.map(flagLine).join("；") }}。
          <b>没有自动改</b> —— 兵力上涨可能是援军抵达或就地整补（合法），也可能是模型漏减（误差）。
          系统不替你判断，只把可疑处摆出来。
        </p>
      </div>

      <nav class="tabs">
        <button class="dock-toggle" @click="dockCollapsed = !dockCollapsed" :aria-expanded="!dockCollapsed">{{ dockCollapsed ? '展开分析台' : '收起分析台' }}</button>
        <button :class="{ active: tab === 'reports' }" @click="tab = 'reports'">📡 动态战报</button>
        <button :class="{ active: tab === 'battlefield' }" @click="tab = 'battlefield'">🗺️ 态势与战损</button>
        <button :class="{ active: tab === 'adjudication' }" @click="tab = 'adjudication'">⚖️ 裁决与守恒</button>
        <button :class="{ active: tab === 'causal' }" @click="tab = 'causal'">因果与轨迹</button>
        <button :class="{ active: tab === 'review' }" @click="tab = 'review'">🦅 参谋长复盘</button>
        <button :class="{ active: tab === 'chat' }" @click="tab = 'chat'">💬 璇玑问答</button>
      </nav>

      <section class="tab-body" v-if="snap" v-show="!dockCollapsed">
        <div v-if="tab === 'reports'" class="reports">
          <p v-if="!snap.history.length" class="muted">尚无战报。请点击「🚀 初始化沙盘」开始推演。</p>
          <article v-for="(h, i) in snap.history" :key="i" class="report" v-html="mdToHtml(h)"></article>
        </div>

        <BattlefieldPanel v-else-if="tab === 'battlefield'" :view="snap.battlefield" />
        <AdjudicationPanel v-else-if="tab === 'adjudication'" :snap="snap" />
        <CausalPanel v-else-if="tab === 'causal'" :session-id="sessionId" :snap="snap" :busy="busy" @busy="busy = $event" />

        <div v-else-if="tab === 'review'" class="review">
          <template v-if="snap.review.text">
            <h4>🦅 战局深度复盘</h4>
            <details v-if="snap.review.reasoning"><summary>💡 参谋长的推演思维链 (CoT)</summary>
              <p v-html="mdToHtml(snap.review.reasoning)"></p></details>
            <article v-html="mdToHtml(snap.review.text)"></article>
          </template>
          <p v-else class="muted">尚未复盘。点「呼叫总参谋长复盘」让深度模型通读全部战报。</p>
        </div>

        <div v-else-if="tab === 'chat'" class="chat">
          <div class="chat-log">
            <p v-if="!snap.chat_messages.length" class="muted">沙盘启动后，可在此直接向总参谋长璇玑提问。</p>
            <div v-for="(m, i) in snap.chat_messages" :key="i" :class="['msg', m.role]">
              <b>{{ m.role === "user" ? "统帅" : "璇玑" }}</b>
              <span v-html="mdToHtml(m.content)"></span>
              <details v-if="m.reasoning"><summary>💡 璇玑思维链 (CoT)</summary>
                <p v-html="mdToHtml(m.reasoning)"></p></details>
            </div>
          </div>
          <div class="chat-input">
            <input v-model="chatInput" placeholder="向总参谋长璇玑提问（如：帮我评估当前优势在哪方？）" @keyup.enter="sendChat" :disabled="!!busy || !hasState()" />
            <button @click="sendChat" :disabled="!!busy || !hasState()">发送</button>
          </div>
        </div>
      </section>

      <div class="toasts">
        <div v-for="(n, i) in notices" :key="i" class="toast">{{ n }}</div>
      </div>
    </main>
  </div>
  </template>
  <dialog ref="settingsDialog" class="settings-dialog tactical-window" aria-labelledby="settings-title">
    <header class="window-heading"><span class="window-index">RUNTIME</span><h2 id="settings-title">DSH · LLM配置</h2><button @click="settingsDialog?.close()" aria-label="关闭模型配置">×</button></header>
    <LlmSettings ref="settingsPanel" embedded :disabled="!!busy" @changed="async () => { health = await api.health(); }" @busy="label => busy = label" />
    <footer class="window-footer"><span>配置保存在本机 · 密钥不回显</span><button @click="settingsDialog?.close()">完成</button></footer>
  </dialog>
  </div>
</template>
