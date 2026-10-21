async page => {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = []; page.on('pageerror', e => errors.push(String(e)));
  await page.setViewportSize({width: 1530, height: 980});
  let first = true;
  await page.route('**/api/llm/config', route => {
    if (first) { first = false; return route.abort('connectionrefused'); }
    return route.continue();
  });
  await page.reload();
  await page.getByRole('button', {name: '模型配置', exact: true}).click();
  await page.locator('#llm-model').waitFor();
  await page.getByRole('button', {name: '完成', exact: true}).click();
  await page.unroute('**/api/llm/config');
  await page.getByRole('button', {name: '返回当前战局', exact: true}).click();
  const clock = await page.locator('.workspace-command-bar').innerText();
  await page.route('**/api/sessions/*/next', route => route.fulfill({status: 502, contentType:'application/json', body:JSON.stringify({detail:'验收：模型连接中断，原战局保留'})}));
  await page.getByRole('button', {name:'下一轮推演'}).click();
  await page.getByText(/验收：模型连接中断，原战局保留/).waitFor();
  assert(await page.locator('.workspace-command-bar').innerText() === clock, 'failed turn changed clock');
  await page.unroute('**/api/sessions/*/next');
  // Inject untrusted model text only into a GET snapshot; never mutate server state.
  await page.route(/\/api\/sessions\/[a-f0-9]{12}$/, async route => {
    if (route.request().method() !== 'GET') return route.continue();
    const response = await route.fetch(); const state = await response.json();
    state.war_state.red_units[0].name = '<img src=x onerror="window.__qaXss=1">';
    await route.fulfill({response, json:state});
  });
  await page.reload();
  await page.getByRole('button', {name:'返回当前战局', exact:true}).click();
  await page.locator('.nato-marker').first().dispatchEvent('click');
  await page.locator('.leaflet-popup-content').waitFor();
  assert((await page.locator('.leaflet-popup-content').innerText()).includes('<img'), 'malicious unit fixture not opened');
  assert(!await page.evaluate(() => window.__qaXss), 'unit text executed');
  assert(await page.locator('.leaflet-popup-content img').count() === 0, 'untrusted popup HTML');
  await page.unroute(/\/api\/sessions\/[a-f0-9]{12}$/);
  await page.route('https://**/*', route => route.abort());
  await page.reload();
  await page.locator('.battle-option').first().waitFor();
  const paths = await page.locator('.leaflet-overlay-pane path').evaluateAll(nodes => nodes.filter(n => n.getBoundingClientRect().width > 0).length);
  assert(paths > 0, 'offline map missing');
  await page.screenshot({path:'.cache/qa/release-3.1.1/offline-final.png'});
  await page.unroute('https://**/*');
  assert(errors.length === 0, errors.join(';'));
  return {config_startup_recovery:true, failed_turn_preserves_clock:true, unit_html_escaped:true, offline_vector_paths:paths, page_errors:errors};
}
