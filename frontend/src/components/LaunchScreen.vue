<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import type { Battle, Health } from '../types';
import { api } from '../api';
import MapView from './MapView.vue';

const props = defineProps<{ battles: Battle[]; health: Health | null; busy: string; error: string; canResume: boolean; recentSessions: { session_id: string; turn_count: number; scenario: string }[] }>();
const emit = defineEmits<{ settings: []; resume: []; reconnect: []; cancel: []; resumeSession: [id: string]; start: [value: { scenario: string; battle: string; time_step: string; use_rag: boolean; use_adjudicator: boolean }] }>();
const minimized = ref(false);
const mode = ref<'catalog' | 'assistant' | 'recent'>('catalog');
const query = ref('');
const family = ref('all');
const phase = ref<'select' | 'configure'>('select');
const selected = ref<Battle | null>(null);
const draft = reactive({ scenario: '', battle: '', time_step: '30分钟', use_rag: true, use_adjudicator: false });
const messages = ref<{ role: string; content: string }[]>([]);
const question = ref('');
const planning = ref(false);
const planError = ref('');
const sources = ref<{ source: string; section: string }[]>([]);
const ready = ref(false);
const steps = ['5分钟', '15分钟', '30分钟', '1小时', '6小时', '12小时', '1天', '1周', '1个月'];
const filtered = computed(() => props.battles.filter(b => (family.value !== 'korea' || b.name.startsWith('抗美援朝-')) && b.name.includes(query.value.trim()))
  .sort((a, b) => (a.name.match(/\d{4}\.\d{2}\.\d{2}/)?.[0] || '').localeCompare(b.name.match(/\d{4}\.\d{2}\.\d{2}/)?.[0] || '')));
function selectBattle(battle: Battle) {
  selected.value = battle;
  Object.assign(draft, { battle: battle.name, scenario: `${battle.name}。按战役开始时的历史态势部署，保留双方兵力、后勤与地形约束。`, time_step: battle.time_step || '30分钟', use_rag: true });
}
function shortName(name: string) { return name.replace(/^抗美援朝-/, '').replace(/-\d{4}.*$/, ''); }
function dates(name: string) { return name.match(/\d{4}[.\d至—-]+/)?.[0] || '历史场景'; }
async function ask() {
  if (!question.value.trim() || planning.value || props.busy) return;
  const submitted = question.value.trim();
  messages.value.push({ role: 'user', content: submitted });
  question.value = ''; planning.value = true; planError.value = '';
  try {
    const data = await api.planScenario(messages.value.slice(-12));
    messages.value.push({ role: 'assistant', content: data.reply });
    ready.value = data.ready; sources.value = data.sources;
    if (data.ready) Object.assign(draft, { scenario: data.scenario, battle: data.battle, time_step: data.time_step });
  } catch (e) { planError.value = String(e); messages.value.pop(); question.value = submitted; }
  finally { planning.value = false; }
}
</script>

<template>
  <main class="launch-screen">
    <div class="launch-map"><MapView :snap="null" :selftest="false" :overview="true" /></div>
    <header class="command-bar">
      <div class="product-mark"><img :src="'./icons/gentactix.png'" alt=""><div><strong>GenTactix <span>DSH</span></strong><small>历史战役推演工作台</small></div></div>
      <nav aria-label="应用菜单"><button class="active" @click="phase = 'select'; minimized = false">想定管理</button><button @click="emit('settings')">模型配置</button><button v-if="canResume" @click="emit('resume')">返回当前战局</button></nav>
      <div class="connection-state"><i :class="{ online: health }"></i>{{ health ? '后端就绪' : '后端未连接' }}</div>
    </header>

    <section v-show="!minimized" class="scenario-window tactical-window" aria-labelledby="scenario-title">
      <div class="window-heading"><span class="window-index">SCENARIO</span><h1 id="scenario-title">{{ phase === 'select' ? '创建推演想定' : '确认想定参数' }}</h1><span class="window-count">{{ phase === 'select' ? '01 / 02' : '02 / 02' }}</span><button @click="minimized = true" :disabled="!!busy" aria-label="收起想定窗口">×</button></div>
      <p v-if="error && phase === 'select'" class="launch-error" role="alert">{{ error }} <button @click="emit('reconnect')">重新连接</button></p>
      <template v-if="phase === 'select'">
        <div class="creation-modes" role="tablist" aria-label="想定创建方式">
          <button role="tab" :aria-selected="mode === 'catalog'" :class="{ active: mode === 'catalog' }" @click="mode = 'catalog'">战役资料库 <span>{{ battles.length }}</span></button>
          <button role="tab" :aria-selected="mode === 'assistant'" :class="{ active: mode === 'assistant' }" @click="mode = 'assistant'">问答生成</button>
          <button v-if="recentSessions.length" role="tab" :aria-selected="mode === 'recent'" :class="{ active: mode === 'recent' }" @click="mode = 'recent'">已保存战局 <span>{{ recentSessions.length }}</span></button>
        </div>
        <div v-if="mode === 'catalog'" class="catalog-body" role="tabpanel">
          <label class="search-field"><span>搜索</span><input v-model="query" placeholder="名称、年代或战役关键词" aria-label="搜索战役"></label>
          <div class="catalog-filter"><button :class="{ active: family === 'all' }" @click="family = 'all'">全部战役</button><button :class="{ active: family === 'korea' }" @click="family = 'korea'">抗美援朝</button><small>{{ filtered.length }} 项</small></div>
          <div class="battle-list" aria-label="可选战役">
            <button v-for="(b, i) in filtered" :key="b.name" class="battle-option" :class="{ selected: selected?.name === b.name }" :aria-pressed="selected?.name === b.name" @click="selectBattle(b)">
              <span class="battle-index">{{ String(i + 1).padStart(2, '0') }}</span><span><strong>{{ shortName(b.name) }}</strong><small>{{ dates(b.name) }} · 建议 {{ b.time_step || '30分钟' }}/回合</small></span><span class="selection-check">{{ selected?.name === b.name ? '已选' : '选择' }}</span>
            </button>
            <p v-if="!filtered.length" class="empty-catalog">{{ battles.length ? '没有匹配的战役。试试其他关键词，或通过问答创建想定。' : '正在载入战役目录；如后端未连接，请先检查服务。' }}</p>
          </div>
          <div class="selection-summary"><small>当前选择</small><strong>{{ selected ? shortName(selected.name) : '请选择一场战役' }}</strong><p>史料、裁决参数与场景范围将随战役载入。</p></div>
        </div>
        <div v-else-if="mode === 'assistant'" class="assistant-body" role="tabpanel">
          <p class="muted">描述战役、时间范围和关注的问题，助手将整理可编辑的想定草案。</p>
          <div class="planning-messages" aria-live="polite">
            <p v-if="!messages.length" class="assistant-placeholder">例如：以1952年上甘岭为背景，重点观察坑道补给与炮兵协同，每15分钟推进一次。</p>
            <article v-for="(m, i) in messages" :key="i" :class="m.role"><small>{{ m.role === 'user' ? '你的需求' : '想定助手' }}</small><p>{{ m.content }}</p></article>
            <p v-if="planning" role="status">正在通过DSH整理想定…</p>
          </div>
          <form @submit.prevent="ask" class="planning-input"><label for="scenario-question">你的想定需求</label><textarea id="scenario-question" v-model="question" rows="3" :disabled="planning || !!busy" placeholder="输入历史背景或希望观察的决策…"></textarea><button :disabled="planning || !!busy || !question.trim()">发送需求</button></form>
          <p v-if="planError" class="bad" role="alert">{{ planError }}</p>
          <small v-if="sources.length" class="muted">本地参考：{{ sources.map(s => s.section).join('、') }}</small>
          <p v-if="ready" class="draft-ready">想定草案已就绪，可进入下一步编辑确认。</p>
        </div>
        <div v-else class="recent-battles" role="tabpanel"><button v-for="s in recentSessions" :key="s.session_id" :disabled="!!busy" @click="emit('resumeSession', s.session_id)"><span><strong>{{ s.scenario }}</strong><small>已推进 {{ s.turn_count }} 回合 · {{ s.session_id }}</small></span><span>打开战局 →</span></button></div>
        <footer class="window-footer"><span>{{ mode === 'recent' ? '恢复保存的态势、回合与分析记录' : '选定想定后，再配置推演规则' }}</span><button v-if="mode !== 'recent'" class="primary" :disabled="!!busy || planning || (mode === 'catalog' ? !selected : !ready)" @click="phase = 'configure'">继续配置 <span>→</span></button></footer>
      </template>
      <template v-else>
        <div class="scenario-form">
          <label for="draft-scenario">想定与推演目标</label><textarea id="draft-scenario" v-model="draft.scenario" rows="6" :disabled="!!busy"></textarea>
          <div class="config-row"><div><label for="draft-battle">关联战役资料</label><select id="draft-battle" v-model="draft.battle" :disabled="!!busy"><option value="">独立想定 / 自动匹配</option><option v-for="b in battles" :key="b.name" :value="b.name">{{ b.name }}</option></select></div><div><label for="draft-step">回合跨度</label><select id="draft-step" v-model="draft.time_step" :disabled="!!busy"><option v-for="step in steps" :key="step">{{ step }}</option></select></div></div>
          <label class="rule-option"><input type="checkbox" v-model="draft.use_rag" :disabled="!!busy"><span><strong>本地史料约束</strong><small>检索关联战役资料，并保留来源</small></span></label>
          <label class="rule-option"><input type="checkbox" v-model="draft.use_adjudicator" :disabled="!!busy"><span><strong>确定性几何裁决</strong><small>裁决机动、接触、补给和包围；不计算确定性伤亡</small></span></label>
          <p class="muted">当前模型：{{ health?.model || '尚未连接' }} · 生成后进入推演工作台。</p>
          <p v-if="busy" class="draft-ready" role="status">{{ busy }}</p><p v-if="error" class="bad" role="alert">{{ error }}</p>
        </div>
        <footer class="window-footer"><button v-if="!busy" @click="phase = 'select'">返回选择</button><button v-else @click="emit('cancel')">取消初始化</button><button class="primary" @click="emit('start', { ...draft })" :disabled="!!busy || !draft.scenario.trim()">{{ busy ? '正在初始化…' : '创建并进入推演' }}</button></footer>
      </template>
    </section>
    <div class="overview-label"><span>中国及周边 · 地理总览</span><small>可拖动与缩放地图</small></div>
    <footer class="launch-status"><span>沈阳理工大学装备工程学院深度学习实验室</span><span>{{ health?.runtime?.connected ? 'DSH · Native已连接' : health?.runtime?.enabled ? 'DSH · 原生插件待启动' : 'DSH · JSON兼容模式' }}</span><a href="https://github.com/pystrategylab/Gentactix" target="_blank" rel="noopener noreferrer">致谢 pystrategylab / Gentactix</a></footer>
  </main>
</template>
