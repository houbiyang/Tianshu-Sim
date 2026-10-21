// Exercise the actual main-process startup functions without launching a model.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../frontend/electron/main.cjs'), 'utf8');
let spawns = 0;
const app = { requestSingleInstanceLock: () => false, quit() {}, on() {} };
function context(fetch, overrides = {}) {
  const sandbox = {
    require(name) {
      if (name === 'electron') return { app: overrides.app || app };
      if (name === 'node:fs' && overrides.fs) return overrides.fs;
      if (name === 'node:child_process') return { spawn: () => { spawns++; }, spawnSync: () => { throw new Error('unexpected process probe'); } };
      return require(name);
    },
    process: { env: {} }, __dirname: path.resolve(__dirname, '../../frontend/electron'),
    fetch, AbortSignal, URL, setTimeout, console,
  };
  vm.createContext(sandbox); vm.runInContext(source, sandbox); return sandbox;
}
(async () => {
  let signalSeen = false;
  await context(async (_url, options) => {
    signalSeen = options.signal instanceof AbortSignal;
    return {ok:true, json:async () => ({engine:'deepseek-harness',status:'ok'})};
  }).startBackend();
  assert.equal(spawns, 0); assert(signalSeen);
  await assert.rejects(context(async () => ({ok:true,json:async () => ({service:'unrelated'})})).startBackend(), /已被其他服务占用/);
  await assert.rejects(context(async () => ({ok:false,json:async () => {throw new Error('not JSON');}})).startBackend(), /已被其他服务占用/);
  assert.equal(spawns, 0);
  let created;
  const installedApp = { ...app, isPackaged: true, getPath: kind => kind === 'exe' ? '/opt/GenTactix-DSH/gentactix-dsh' : '/home/test/.config/GenTactix-DSH' };
  const installed = context(null, { app: installedApp, fs: { ...fs,
    existsSync: () => false, accessSync: () => { throw new Error('EACCES'); },
    mkdirSync: directory => { created = directory; } } });
  assert.equal(installed.projectRoot(), '/home/test/.config/GenTactix-DSH/project');
  assert.equal(created, installed.projectRoot());
  const portable = context(null, { app: installedApp, fs: { ...fs, existsSync: () => false, accessSync() {} } });
  assert.equal(portable.projectRoot(), '/opt/GenTactix-DSH');
  console.log(JSON.stringify({existing_backend_reused:true,bounded_probe:true,foreign_service_rejected:true,invalid_health_rejected:true,installed_workspace_writable:true,portable_workspace_preserved:true}));
})().catch(error => { console.error(error); process.exitCode = 1; });
