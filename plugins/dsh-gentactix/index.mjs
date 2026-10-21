/** Cordis plugin: native domain tools, per-run policy, live cancellation and forks.
 * No dependencies on DSH internals: uses the injected public runtime services.
 */
import http from 'node:http';
import { writeFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

export const name = 'gentactix-runtime';
export const inject = ['tools', 'agents', 'sessions'];

const object = { type: 'object', additionalProperties: true };
const definitions = [
  ['get_observation', '读取本次决策前单位状态；只读。', { names: { type: 'array', items: { type: 'string' } } }],
  ['retrieve_history', '检索当前战役史料与既往战报；只读。', { query: { type: 'string' } }],
  ['evaluate_action', '按当前几何参数试算候选位移，不提交战局。', { moves: { type: 'array', items: object } }],
  ['inspect_trajectory', '核对已提交决策的轨迹、状态变化与证据编号。', { turn: { type: 'integer', minimum: 0 } }],
  ['evaluate_counterfactual', '撤销已提交位移，固定其他终态，运行单步几何归因试算。', { turn: { type: 'integer', minimum: 1 }, undo: { type: 'array', items: object } }],
];

export function apply(ctx) {
  const token = process.env.GENTACTIX_CONTROL_TOKEN;
  const bridge = process.env.GENTACTIX_TOOL_BRIDGE;
  const readyFile = process.env.GENTACTIX_CONTROL_FILE;
  if (!token || !bridge || !readyFile) throw new Error('GenTactix runtime bridge is not configured');
  const policies = new Map();
  const children = new Map();
  const counters = { events: 0, calls: 0, cancellations: 0, forks: 0 };
  const disposers = [];

  for (const [toolName, description, properties] of definitions) {
    disposers.push(ctx.tools.register({
      name: toolName, description,
      parameters: { type: 'object', properties, additionalProperties: false },
      output: { schema: object, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
      async execute(args, exec) {
        const sessionId = String(exec.agent?.session?.id ?? '');
        const policy = policies.get(sessionId);
        if (!policy?.allowed_tools?.includes(toolName)) throw new Error('本次操作未启用该只读领域工具');
        policy.calls = (policy.calls ?? 0) + 1;
        if (policy.calls > policy.max_tool_calls) throw new Error('本次操作的领域工具预算已用尽');
        counters.calls++;
        const response = await fetch(bridge + '/tool', {
          method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
          body: JSON.stringify({ session_id: sessionId, tool: toolName, args }), signal: exec.signal,
        });
        if (!response.ok) throw new Error(`领域工具桥接失败：HTTP ${response.status}`);
        return await response.json();
      },
    }));
  }

  // Policy belongs in the runtime request waterfall, not in prompt wording.
  disposers.push(ctx.on('agent/request', async ({ agent }, next) => {
    const config = await next();
    const policy = policies.get(String(agent.session.id));
    if (!policy) return config;
    return { ...config, maxTokens: policy.max_tokens,
      ...(policy.reasoning_effort ? { reasoningEffort: policy.reasoning_effort } : {}) };
  }));
  disposers.push(ctx.on('agent/pre-step', async ({ agent, step }, next) => {
    const policy = policies.get(String(agent.session.id));
    if (policy && step > policy.max_steps) throw new Error('GenTactix 模型步骤预算已用尽');
    return next();
  }));
  disposers.push(ctx.on('session/event', () => { counters.events++; }));

  async function readBody(req) {
    let raw = '';
    for await (const chunk of req) {
      raw += chunk;
      if (raw.length > 4_000_000) throw new Error('control request too large');
    }
    return raw ? JSON.parse(raw) : {};
  }
  const server = http.createServer(async (req, res) => {
    const send = (code, value) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
    if (req.headers.authorization !== `Bearer ${token}`) return send(401, { error: 'unauthorized' });
    try {
      const body = await readBody(req);
      if (req.url === '/health') return send(200, { plugin: name, mode: 'native', ptc: false,
        tools: definitions.map(x => x[0]), capabilities: ['cancel', 'fork', 'request-policy', 'events'], counters });
      if (req.url === '/configure') {
        if (typeof body.session_id !== 'string') throw new Error('session_id is required');
        policies.set(body.session_id, { ...body.policy, calls: 0 });
        return send(200, { ok: true });
      }
      if (req.url === '/cancel') {
        const agent = ctx.agents.get(body.session_id);
        if (!agent) return send(404, { error: 'active runtime session not found' });
        agent.cancel({ kind: 'user' });
        counters.cancellations++;
        return send(200, { ok: true, mode: 'native-agent-cancel', session_id: body.session_id });
      }
      if (req.url === '/fork-run') {
        const parent = ctx.agents.get(body.parent_session_id);
        if (!parent) return send(404, { error: 'fork parent is not live; resume it first' });
        const all = parent.session.snapshotEvents();
        const boundary = body.boundary;
        if (!Number.isInteger(boundary) || boundary < 0 || boundary >= all.length) throw new Error('invalid exact fork boundary');
        const prefix = all.slice(0, boundary + 1);
        let open = false;
        for (const event of prefix) { if (event.type === 'turn/start') open = true; if (event.type === 'turn/end') open = false; }
        if (open) throw new Error('fork boundary must be outside an open turn');
        const id = body.session_id;
        if (typeof id !== 'string' || !id.startsWith('gentactix-')) throw new Error('invalid branch id');
        // Public AgentRegistry factory seed contract: exact inherited prefix plus native marker.
        const seed = [...prefix, { type: 'session/end-seed', seq: prefix.length, time: Date.now(), data: { inherited: true } }];
        const handle = await ctx.agents.create({ sessionId: id, parentAgent: parent,
          meta: { cwd: parent.session.header.cwd, parentSession: parent.session.id, isSeeded: true },
          inheritedEventCount: prefix.length, seed, agentOptions: { ...parent.options } });
        children.set(id, handle);
        policies.set(id, { ...body.policy, calls: 0 });
        counters.forks++;
        const agent = handle.agent;
        agent.followup({ id: randomUUID(), role: 'user', content: [{ type: 'text', text: body.prompt }], source: { kind: 'user' } });
        await agent.whenIdle();
        const events = agent.session.snapshotEvents(prefix.length + 1);
        const end = [...events].reverse().find(x => x.type === 'turn/end');
        const message = [...events].reverse().find(x => x.type === 'assistant/message');
        const blocks = message?.data?.message?.content ?? message?.data?.content ?? [];
        const final_response = blocks.filter(x => x.type === 'text').map(x => x.text ?? '').join('\n');
        const reason = end?.data?.reason;
        return send(200, { session_id: id, parent_session_id: body.parent_session_id, boundary,
          finish_reason: typeof reason === 'string' ? reason : reason?.kind, final_response, events });
      }
      return send(404, { error: 'unknown control operation' });
    } catch (error) { return send(400, { error: String(error?.message ?? error) }); }
  });
  server.listen(0, '127.0.0.1', () => {
    writeFileSync(readyFile, JSON.stringify({ port: server.address().port, plugin: name }), { mode: 0o600 });
  });
  ctx.on('dispose', async () => {
    for (const dispose of disposers) dispose();
    for (const handle of children.values()) await handle.dispose?.();
    server.close();
  });
}
