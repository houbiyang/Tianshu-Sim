async page => {
  const API='http://127.0.0.1:18880', UI='http://127.0.0.1:18884';
  const results=[], errors=[];
  page.setDefaultTimeout(7000);
  page.on('pageerror', e=>errors.push(String(e)));
  await page.setViewportSize({width:1530,height:980});
  const assert=(ok,msg)=>{if(!ok)throw new Error(msg);};
  const clean=o=>({...(o.id?{id:o.id}:{}),unit_id:o.unit_id,action:o.action,duration:o.duration,
    ...(o.destination?{destination:o.destination}:{}),...(o.target_id?{target_id:o.target_id}:{})});
  const rev=v=>({expected_turn:v.turn,revision:v.revision,world_version:v.world_version});
  const next=()=>page.getByRole('button',{name:'⏩ 下一轮推演',exact:true});
  const upload=(input,contents,name='battle.json')=>input.evaluate((node,args)=>{
    const transfer=new DataTransfer();transfer.items.add(new File([args.contents],args.name,{type:'application/json'}));
    node.files=transfer.files;node.dispatchEvent(new Event('change',{bubbles:true}));
  },{contents,name});
  const units=()=>page.getByRole('combobox',{name:'单位名单（含无坐标单位）'});
  async function load(sid) {
    await page.addInitScript(id=>{window.gentactix={backendUrl:'http://127.0.0.1:18880'};localStorage.setItem('gentactix-session',id);},sid);
    await page.goto(UI,{waitUntil:'domcontentloaded'});
    await page.getByRole('tab',{name:/已保存战局/}).click();
    await page.locator('button').filter({hasText:sid}).first().click();
    await page.getByTestId('mission-panel').waitFor();
    await page.getByTestId('command-toggle').click();
    await units().selectOption({label:'红 · 红方教学编组1'});
  }
  async function setup() {
    await page.unrouteAll({behavior:'wait'});
    const r=await page.request.post(API+'/api/sessions',{data:{scenario:'TDD 命令恢复隔离验收',mission_id:'AJ15-local-v1',persist_roles:false,use_adjudicator:true}});
    assert(r.ok(),await r.text());const sid=(await r.json()).session_id,path=API+'/api/sessions/'+sid;
    const init=await page.request.post(path+'/init');assert(init.ok(),await init.text());
    await load(sid);
    return {sid,path,get:async()=>await(await page.request.get(path+'/commands')).json()};
  }
  async function test(name,fn){try{await fn();results.push({case:name,passed:true});}catch(e){results.push({case:name,passed:false,error:String(e)});}}
  async function draft() {
    await page.getByTestId('unit-action').selectOption('move');
    await page.getByRole('button',{name:'地图选目的地',exact:true}).click();
    const b=await page.getByRole('button',{name:'红方 红方教学编组1',exact:true}).boundingBox();assert(b,'marker missing');
    await page.mouse.click(b.x+b.width+16,b.y+b.height+12);
    await page.getByTestId('command-panel').waitFor({state:'visible'});
    await page.locator('summary').filter({hasText:'自然语言：单位／全局控制'}).click();
    await page.getByRole('textbox',{name:'指挥内容'}).fill('前往选定位置，持续移动');
    await page.getByRole('button',{name:'DSH 解析为草稿',exact:true}).click();
    await page.getByText('待确认草稿',{exact:true}).waitFor();
  }
  await test('editing text and failed parse cannot confirm old move',async()=>{
    const s=await setup();await draft();
    await page.getByRole('textbox',{name:'指挥内容'}).fill('不要移动，保持待命');
    assert(await page.getByRole('button',{name:'确认草稿并入队',exact:true}).count()===0,'old draft survived text edit');
    await page.route('**/commands/resolve',r=>r.fulfill({status:502,contentType:'application/json',body:JSON.stringify({detail:'注入解析故障'})}));
    await page.getByRole('button',{name:'DSH 解析为草稿',exact:true}).click();
    await page.getByTestId('command-panel').getByRole('alert').filter({hasText:'注入解析故障'}).waitFor();
    assert(await page.getByRole('button',{name:'确认草稿并入队',exact:true}).count()===0,'failed parse left confirmable draft');
    assert((await s.get()).orders.length===0,'parse mutated queue');
  });
  await test('in-flight parse cannot return draft into changed scope',async()=>{
    await setup();await draft();
    await page.route('**/commands/resolve',async r=>{const response=await r.fetch();await page.waitForTimeout(250);await r.fulfill({response});});
    const requested=page.waitForRequest('**/commands/resolve');
    await page.getByRole('button',{name:'DSH 解析为草稿',exact:true}).click();await requested;
    await page.getByRole('combobox',{name:'范围',exact:true}).selectOption('global');
    await page.getByRole('button',{name:'DSH 解析为草稿',exact:true}).waitFor({state:'visible'});
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(x=>x.textContent==='DSH 解析为草稿')?.disabled);
    assert(await page.getByRole('button',{name:'确认草稿并入队',exact:true}).count()===0,'late draft accepted after scope changed');
  });
  await test('known 409 refresh creates new batch and progresses once',async()=>{
    const s=await setup();await page.getByTestId('enqueue-order').click();await page.locator('[data-command-id]').waitFor();
    const v=await s.get();const changed=await page.request.put(s.path+'/commands',{data:{...rev(v),orders:v.orders.map(clean),intent:'另一客户端修改'}});assert(changed.ok(),await changed.text());
    const requests=[];const listener=r=>{if(r.url()===s.path+'/next')requests.push(r.postDataJSON());};page.on('request',listener);
    try {
      await next().click();await page.locator('aside .error').filter({hasText:'版本已改变'}).waitFor();
      await page.getByRole('button',{name:'刷新命令',exact:true}).click();
      await next().click();await page.waitForFunction(()=>document.querySelector('[data-testid=mission-panel]')?.textContent.includes('1 / 4'));
      assert(requests.length===2&&requests[0].batch_id!==requests[1].batch_id,'known rejection reused stale batch');
      assert((await s.get()).turn===1,'retry did not advance exactly once');
    } finally {page.off('request',listener);}
  });
  await test('lost response and refresh preserve idempotency batch',async()=>{
    const s=await setup();await page.getByTestId('enqueue-order').click();await page.locator('[data-command-id]').waitFor();
    const requests=[];const listener=r=>{if(r.url()===s.path+'/next')requests.push(r.postDataJSON());};page.on('request',listener);
    let dropped=false;
    await page.route('**/next',async route=>{if(!dropped){dropped=true;const result=await route.fetch();assert(result.ok(),await result.text());await route.abort('failed');}else await route.continue();});
    try {
      await next().click();await page.locator('aside .error').waitFor();
      assert((await s.get()).turn===1,'injected lost response did not commit');
      await page.getByRole('button',{name:'刷新命令',exact:true}).click();
      await next().click();await page.waitForFunction(()=>document.querySelector('[data-testid=mission-panel]')?.textContent.includes('1 / 4'));
      assert(requests.length===2&&requests[0].batch_id===requests[1].batch_id,'unknown result lost original idempotency key');
      assert((await s.get()).turn===1,'lost response retry advanced a second time');
    } finally {page.off('request',listener);}
  });
  await test('unit and turn changes invalidate labelled comparison',async()=>{
    const s=await setup();await page.getByTestId('enqueue-order').click();await page.locator('[data-command-id]').waitFor();
    await next().click();await page.waitForFunction(()=>document.querySelector('[data-testid=mission-panel]')?.textContent.includes('1 / 4'));
    await page.getByRole('button',{name:'只读对照：上轮本单位待命',exact:true}).click();
    const result=page.getByTestId('unit-comparison');await result.waitFor();
    assert((await result.innerText()).includes('红方教学编组1')&&(await result.innerText()).includes('T+1'),'comparison provenance missing');
    await units().selectOption({label:'红 · 红方教学编组2'});assert(await result.count()===0,'comparison survived unit change');
    await page.getByRole('button',{name:'只读对照：上轮本单位待命',exact:true}).click();await result.waitFor();
    await next().click();await page.waitForFunction(()=>document.querySelector('[data-testid=mission-panel]')?.textContent.includes('2 / 4'));
    assert(await result.count()===0,'comparison survived turn change');
    assert((await s.get()).turn===2,'compare changed clock');
  });
  await test('scope switch previews and explicitly cleans affected commands and agents',async()=>{
    const s=await setup();let v=await s.get();const red=v.units.filter(u=>u.team==='red').slice(0,2),blue=v.units.find(u=>u.team==='blue');
    const saved=await page.request.put(s.path+'/commands',{data:{...rev(v),director:true,control_team:'red',orders:[...red,blue].map(u=>({unit_id:u.id,action:'hold'})),unit_agents:[...red.map(u=>u.id),blue.id]}});assert(saved.ok(),await saved.text());
    await load(s.sid);
    await page.locator('summary').filter({hasText:'控制范围与全局约束'}).click();
    await page.getByRole('combobox',{name:'控制方',exact:true}).selectOption('blue');
    await page.getByRole('checkbox',{name:'教学导演：允许控制双方'}).uncheck();
    const preview=page.getByTestId('scope-transition');await preview.waitFor();
    assert((await preview.innerText()).includes(red[0].name),'affected unit not named');
    await preview.scrollIntoViewIfNeeded();
    await page.screenshot({path:'.cache/qa/tdd-20261025/scope-transition-wide.png'});
    assert((await s.get()).unit_agents.length===3,'preview changed binding');
    await page.getByRole('button',{name:'取消切换',exact:true}).click();assert((await s.get()).unit_agents.length===3,'cancel changed binding');
    await page.getByRole('combobox',{name:'控制方',exact:true}).selectOption('blue');
    await page.getByRole('checkbox',{name:'教学导演：允许控制双方'}).uncheck();
    await page.getByRole('button',{name:'确认切换并处理旧命令与 Agent',exact:true}).click();
    await preview.waitFor({state:'hidden'});v=await s.get();
    assert(v.control_team==='blue'&&!v.director&&v.unit_agents.length===1&&v.unit_agents[0]===blue.id,'switch lost retained agents');
    assert(v.orders.length===1&&v.orders[0].unit_id===blue.id&&v.turn===0,'switch did not clean precisely');
  });
  await test('import has preview cancel duplicate invalid and confirmed restore',async()=>{
    const s=await setup();await page.getByTestId('enqueue-order').click();await page.locator('[data-command-id]').waitFor();
    const exported=await(await page.request.get(s.path+'/export')).json();
    const bytes=JSON.stringify(exported);
    const open=()=>page.getByRole('button',{name:'导入战局',exact:true}).click();
    await open();let dialog=page.getByRole('dialog',{name:'导入已保存战局',exact:true});await dialog.waitFor();
    await upload(dialog.getByLabel('战局 JSON 文件', {exact:true}),bytes);
    await dialog.getByTestId('import-preview').waitFor();
    assert(await dialog.getByRole('button',{name:'确认导入并打开',exact:true}).isDisabled(),'duplicate import enabled');
    await page.screenshot({path:'.cache/qa/tdd-20261025/import-preview-wide.png'});
    await page.setViewportSize({width:390,height:844});
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'narrow import causes page overflow');
    const box=await dialog.boundingBox();assert(box&&box.width<=390,'import dialog does not fit narrow viewport');
    await page.screenshot({path:'.cache/qa/tdd-20261025/import-preview-narrow.png'});
    await page.setViewportSize({width:1530,height:980});
    await dialog.getByRole('button',{name:'取消',exact:true}).click();
    assert((await s.get()).orders.length===1,'cancel mutated original');
    const deleted=await page.request.delete(s.path);assert(deleted.ok(),await deleted.text());
    await open();dialog=page.getByRole('dialog',{name:'导入已保存战局',exact:true});
    await upload(dialog.getByLabel('战局 JSON 文件',{exact:true}),'{bad','bad.json');
    await dialog.getByRole('alert').waitFor();assert(await dialog.getByRole('button',{name:'确认导入并打开',exact:true}).isDisabled(),'invalid file confirm enabled');
    await upload(dialog.getByLabel('战局 JSON 文件',{exact:true}),bytes);await dialog.getByTestId('import-preview').waitFor();
    assert(!(await page.request.get(s.path)).ok(),'preview wrote a session');
    await dialog.getByRole('button',{name:'确认导入并打开',exact:true}).click();await dialog.waitFor({state:'hidden'});
    const restored=await(await page.request.get(s.path)).json();assert(restored.turn_count===0,'import changed clock');
    assert((await s.get()).orders.length===1,'import lost queued command');
    await page.getByRole('button',{name:'想定管理',exact:true}).click();assert(await page.getByRole('button',{name:'导入战局',exact:true}).isVisible(),'launch import missing');
  });
  assert(errors.length===0,errors.join('\n'));
  return {passed:results.every(r=>r.passed),results,errors};
}
