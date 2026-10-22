<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue';
import { api } from '../api';
import type { LlmConfig } from '../types';

const props = defineProps<{ disabled: boolean; embedded?: boolean }>();
const emit = defineEmits<{ changed: []; busy: [label: string] }>();
const open = ref(!!props.embedded);
const current = ref<LlmConfig | null>(null);
const form = reactive({ base_url: '', model: '', api_key: '', max_tokens: 65536, context_window: 262144, timeout_seconds: 900,
  clear_api_key: false, max_steps: 8, max_tool_calls: 16, role_policies: {} as LlmConfig['role_policies'] });
const roleNames: Record<string, string> = { engine: '战场裁决', strategist: '战略研判', chat: '战局问答', planner: '想定生成', causal: '因果分析', counterfactual: '反事实分支', 'connection-test': '连接测试' };
const preset = ref('vllm');
const working = ref(false);
const message = ref('');
const error = ref('');

async function load() {
  if (working.value) return;
  error.value = ''; message.value = '';
  try {
    current.value = await api.llmConfig();
    Object.assign(form, { ...current.value, api_key: '' });
    preset.value = current.value.provider;
  } catch (e) { error.value = `无法读取模型配置，请确认后端就绪后重试：${e}`; }
}
defineExpose({ reload: load });
onMounted(load);
function choosePreset() {
  const item = current.value?.presets.find(p => p.id === preset.value);
  if (item) Object.assign(form, { base_url: item.base_url, model: item.model, api_key: '' });
  message.value = ''; error.value = '';
}
function useBattleBudget() {
  const budget = Math.min(form.context_window, Math.max(form.max_tokens, 65536));
  form.max_tokens = budget;
  form.role_policies.engine = { max_tokens: budget,
    reasoning_effort: form.role_policies.engine ? form.role_policies.engine.reasoning_effort : 'high' };
  message.value = '已同步全局与裁决角色预算，保留当前推理强度。点击保存后生效。';
}
async function save() {
  working.value = true; error.value = ''; message.value = '';
  try {
    current.value = await api.saveLlmConfig({ base_url: form.base_url, model: form.model, api_key: form.api_key || null,
      max_tokens: form.max_tokens, context_window: form.context_window, timeout_seconds: form.timeout_seconds,
      clear_api_key: form.clear_api_key, role_policies: form.role_policies, max_steps: form.max_steps, max_tool_calls: form.max_tool_calls });
    Object.assign(form, { ...current.value, api_key: '', clear_api_key: false });
    message.value = '配置已加密保存，下一次调用使用新配置。战局已保留。';
    emit('changed');
  } catch (e) { error.value = String(e); }
  finally { working.value = false; }
}
async function testConnection() {
  working.value = true; error.value = ''; message.value = ''; emit('busy', '正在通过DSH测试当前连接…');
  try {
    const result = await api.testLlm();
    message.value = `DSH真实连接通过：${result.model}`;
  } catch (e) { error.value = String(e); }
  finally { working.value = false; emit('busy', ''); }
}
</script>

<template>
  <section class="llm-settings">
    <button v-if="!embedded" class="link" @click="open = !open" :aria-expanded="open">DSH · LLM配置 {{ open ? '▾' : '▸' }}</button>
    <div v-if="open">
      <template v-if="current">
        <p class="muted">当前生效：{{ current.model }}<br>{{ current.base_url }}</p>
        <label for="llm-preset">接口预设</label>
        <select id="llm-preset" v-model="preset" @change="choosePreset" :disabled="disabled || working">
          <option v-for="p in current.presets" :key="p.id" :value="p.id">{{ p.label }}</option>
        </select>
        <label for="llm-url">API Base URL</label>
        <input id="llm-url" v-model="form.base_url" type="url" :disabled="disabled || working" placeholder="http://127.0.0.1:8000/v1">
        <label for="llm-model">模型ID</label>
        <input id="llm-model" v-model="form.model" :disabled="disabled || working" placeholder="服务实际提供的模型名称">
        <label for="llm-key">API Key · {{ current.api_key_configured ? '已配置' : '未配置' }}</label>
        <input id="llm-key" v-model="form.api_key" type="password" autocomplete="new-password" :disabled="disabled || working" placeholder="同一地址留空保留已有密钥">
        <label class="check"><input type="checkbox" v-model="form.clear_api_key" :disabled="disabled || working">清除已有密钥（无鉴权vLLM）</label>
        <p class="muted">同一地址留空保留已有密钥；更换地址需重新填写。密钥不回显，配置使用认证加密保存，解密密钥存放于独立的用户配置目录。</p>
        <label for="llm-max-tokens">全局输出token上限（含推理与正文）</label>
        <input id="llm-max-tokens" v-model.number="form.max_tokens" type="number" min="128" max="262144" :disabled="disabled || working">
        <p v-if="form.role_policies.engine" class="muted">战场裁决有效上限：{{ Math.min(form.max_tokens, form.role_policies.engine.max_tokens) }} token（取全局与角色上限较小值）。只提高全局上限不会提高已保存的角色上限。</p>
        <button type="button" @click="useBattleBudget" :disabled="disabled || working">填写裁决建议预算</button>
        <p class="muted">建议为完整战局预留 65,536 token；此按钮保留当前推理强度。上限不是固定消耗，仍受服务实际容量限制。</p>
        <label for="llm-context">上下文容量</label>
        <input id="llm-context" v-model.number="form.context_window" type="number" min="1024" max="2000000" :disabled="disabled || working">
        <label for="llm-timeout">超时（秒）</label>
        <input id="llm-timeout" v-model.number="form.timeout_seconds" type="number" min="10" max="1800" :disabled="disabled || working">
        <details class="llm-advanced">
          <summary>角色预算与运行控制</summary>
          <label for="llm-steps">每次运行步骤上限</label>
          <input id="llm-steps" v-model.number="form.max_steps" type="number" min="1" max="64" :disabled="disabled || working">
          <label for="llm-tools">每次运行工具调用上限</label>
          <input id="llm-tools" v-model.number="form.max_tool_calls" type="number" min="0" max="128" :disabled="disabled || working">
          <div v-for="(policy, role) in form.role_policies" :key="role" class="role-budget">
            <label :for="`budget-${role}`">{{ roleNames[role] || role }} · 输出token</label>
            <input :id="`budget-${role}`" v-model.number="policy.max_tokens" type="number" min="128" max="262144" :disabled="disabled || working">
            <label :for="`effort-${role}`">推理强度</label>
            <select :id="`effort-${role}`" v-model="policy.reasoning_effort" :disabled="disabled || working">
              <option :value="null">继承DSH默认</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="xhigh">极高</option>
            </select>
          </div>
          <p class="muted">角色输出受全局上限约束。继承DSH默认可能启用高推理强度，并不代表关闭推理。推理强度是否受支持由模型服务决定。</p>
        </details>
        <p v-if="current.storage" class="muted">加密配置：{{ current.storage.path }}</p>
        <p v-if="current.storage?.error" class="bad" role="alert">{{ current.storage.error }}</p>
        <p class="muted">DeepSeek当前推荐ID为deepseek-flash；也可填写兼容别名deepseek-v4-flash。vLLM需填写实际served-model-name。先保存，再测试当前连接。</p>
        <div class="llm-actions">
          <button @click="save" :disabled="disabled || working">保存配置</button>
          <button @click="testConnection" :disabled="disabled || working">测试当前连接</button>
        </div>
      </template>
      <p v-if="message" role="status">{{ message }}</p>
      <p v-if="error" class="bad" role="alert">{{ error }}</p>
      <button v-if="!current" @click="load" :disabled="working">重新读取配置</button>
    </div>
  </section>
</template>

<style scoped>
.llm-settings { border-top: 1px solid var(--border, #343b49); padding-top: 12px; margin: 12px 0; }
.llm-settings input { box-sizing: border-box; width: 100%; }
.llm-settings p { overflow-wrap: anywhere; }
.llm-settings .check input { width: auto; }
.llm-advanced { margin: 14px 0; }
.llm-advanced summary { cursor: pointer; }
.role-budget { padding: 10px 0; border-bottom: 1px solid var(--border); }
.llm-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
</style>
