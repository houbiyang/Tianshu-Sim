<script setup lang="ts">
import { nextTick, ref } from 'vue';
import { api } from '../api';
import type { ImportPreview, Snapshot } from '../types';

const emit = defineEmits<{ imported: [value: Snapshot] }>();
const dialog = ref<HTMLDialogElement | null>(null), fileInput = ref<HTMLInputElement | null>(null);
const snapshotInput = ref<HTMLInputElement | null>(null);
const preview = ref<ImportPreview | null>(null), error = ref(''), busy = ref(false), committedId = ref('');
let document: Record<string, unknown> | null = null, snapshot: Record<string, unknown> | null = null;
let generation = 0;
function payload() { return snapshot ? { export: document, snapshot } : document!; }
async function open() {
  generation++; document = null; snapshot = null; preview.value = null; error.value = ''; committedId.value = '';
  if (fileInput.value) fileInput.value.value = '';
  if (snapshotInput.value) snapshotInput.value.value = '';
  dialog.value?.showModal(); await nextTick(); fileInput.value?.focus();
}
function close() { if (!busy.value) { generation++; dialog.value?.close(); } }
async function read(event: Event, legacy = false) {
  const ticket = ++generation;
  preview.value = null; error.value = '';
  if (legacy) snapshot = null; else document = null;
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  busy.value = true;
  try {
    if (file.size > 64 * 1024 * 1024) throw new Error('文件超过 64 MiB，请选择单个战局导出文件');
    let value: unknown;
    try { value = JSON.parse(await file.text()); } catch { throw new Error('无法读取 JSON，请选择有效的战局文件'); }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('JSON 顶层必须是对象');
    if (ticket !== generation) return;
    if (legacy) snapshot = value as Record<string, unknown>; else document = value as Record<string, unknown>;
    if (!document) throw new Error('请先选择战局 JSON 文件');
    const result = await api.previewImport(payload());
    if (ticket === generation) preview.value = result;
  } catch (e) { if (ticket === generation) error.value = String(e); }
  finally { if (ticket === generation) busy.value = false; }
}
async function confirm() {
  if (busy.value || (!committedId.value && (!preview.value || preview.value.conflict))) return;
  busy.value = true; error.value = '';
  try {
    if (!committedId.value) {
      const result = await api.importSession(payload());
      committedId.value = result.session_id;
    }
    const restored = await api.get(committedId.value);
    emit('imported', restored); dialog.value?.close();
  } catch (e) {
    error.value = committedId.value ? `战局已导入，打开失败；可重试打开或从已保存战局恢复。${e}` : String(e);
  } finally { busy.value = false; }
}
defineExpose({ open });
</script>

<template>
  <dialog ref="dialog" class="settings-dialog tactical-window import-dialog" aria-labelledby="import-title"
    @cancel="event => { if (busy) event.preventDefault(); else generation++; }">
    <header class="window-heading"><span class="window-index">RESTORE SESSION</span><h2 id="import-title">导入已保存战局</h2></header>
    <div class="import-content">
      <p>选择导出的 JSON，核对预览后确认恢复。不会覆盖同 ID 战局或修改当前打开的战局。</p>
      <label>战局 JSON 文件<input ref="fileInput" type="file" accept=".json,application/json" :disabled="busy || !!committedId" @change="read($event)" /></label>
      <details><summary>旧版导出兼容</summary><p>旧版文件未记录完整规则配置，还需选择当时保存的原会话快照 JSON。</p>
        <label>原会话快照 JSON<input ref="snapshotInput" type="file" accept=".json,application/json" :disabled="busy || !!committedId" @change="read($event, true)" /></label>
      </details>
      <p v-if="busy" role="status">{{ committedId ? '正在打开已导入战局…' : '正在校验文件…' }}</p>
      <p v-if="error" class="bad" role="alert">{{ error }}</p>
      <section v-if="preview" data-testid="import-preview" aria-label="导入预览">
        <h3>{{ preview.scenario }}</h3><p>{{ preview.session_id }} · T+{{ preview.turn_count }} · {{ preview.time_step }} · {{ preview.simulation_mode === 'open' ? '开放推演' : '历史回放' }}</p>
        <p>{{ preview.unit_count }} 个单位 · {{ preview.order_count }} 项命令 · {{ preview.agent_count }} 个单位 Agent</p>
        <p v-if="preview.conflict" class="bad" role="alert">该 ID 已存在，不能导入。请从已保存战局打开，或选择其他文件。</p>
        <p v-for="warning in preview.warnings" :key="warning" class="muted">{{ warning }}</p>
      </section>
    </div>
    <footer class="window-footer"><button :disabled="busy" @click="close">{{ committedId ? '关闭' : '取消' }}</button>
      <button class="primary" :disabled="busy || (!committedId && (!preview || preview.conflict))" @click="confirm">{{ committedId ? '重试打开已导入战局' : '确认导入并打开' }}</button></footer>
  </dialog>
</template>

<style scoped>
.import-content { padding: 18px 24px; max-height: 65vh; overflow: auto; }
label { display: block; margin: 18px 0; }
input { display: block; margin-top: 8px; width: 100%; }
section { border-top: 1px solid var(--line); margin-top: 16px; padding-top: 10px; overflow-wrap: anywhere; }
</style>
