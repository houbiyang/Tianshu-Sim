async page=>{
 const API='http://127.0.0.1:18880';
 const assert=(ok,message)=>{if(!ok)throw new Error(message);};
 assert(await page.evaluate(()=>location.protocol==='file:'&&window.isSecureContext&&typeof crypto.randomUUID==='function'),'native file renderer cannot create batch IDs');
 assert(await page.evaluate(()=>typeof require==='undefined'&&typeof process==='undefined'),'Node leaked into renderer');
 const result=await page.request.post(API+'/api/sessions',{data:{scenario:'同回合导入刷新验收',mission_id:'AJ15-local-v1',persist_roles:false}});
 assert(result.ok(),await result.text());
 const sid=(await result.json()).session_id,path=API+'/api/sessions/'+sid;
 await page.request.post(path+'/init');
 let v=await(await page.request.get(path+'/commands')).json();
 await page.request.put(path+'/commands',{data:{expected_turn:v.turn,revision:v.revision,world_version:v.world_version,orders:[{unit_id:v.units[0].id,action:'hold'}]}});
 const document=await(await page.request.get(path+'/export')).json();
 v=await(await page.request.get(path+'/commands')).json();
 await page.request.delete(path+'/commands/'+v.orders[0].id,{data:{expected_turn:v.turn,revision:v.revision,world_version:v.world_version}});
 await page.evaluate(id=>localStorage.setItem('gentactix-session',id),sid);await page.reload();
 await page.getByRole('tab',{name:/已保存战局/}).click();await page.locator('button').filter({hasText:sid}).first().click();
 await page.getByTestId('command-toggle').click();await page.getByRole('button',{name:'刷新命令',exact:true}).waitFor();
 await page.request.delete(path);await page.getByRole('button',{name:'导入战局',exact:true}).click();
 const d=page.getByRole('dialog',{name:'导入已保存战局',exact:true});
 await d.getByLabel('战局 JSON 文件',{exact:true}).evaluate((node,json)=>{const dt=new DataTransfer();dt.items.add(new File([json],'session.json',{type:'application/json'}));node.files=dt.files;node.dispatchEvent(new Event('change',{bubbles:true}));},JSON.stringify(document));
 await d.getByTestId('import-preview').waitFor();await d.getByRole('button',{name:'确认导入并打开',exact:true}).click();await d.waitFor({state:'hidden'});
 await page.waitForFunction(()=>document.querySelectorAll('[data-command-id]').length===1,{},{timeout:2500});
 await page.getByRole('button',{name:'⏩ 下一轮推演',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('[data-testid=mission-panel]')?.textContent.includes('1 / 4'));
 const restored=await(await page.request.get(path)).json();assert(restored.turn_count===1,'native imported turn did not advance');
 assert((await(await page.request.get(path+'/unit-decisions')).json()).total===6,'imported turn lost unit decisions');
 await page.screenshot({path:'.cache/qa/tdd-20261025/electron-workspace.png'});
 return{passed:true,same_turn_import_refresh:true,native_file_import:true,native_command_turn:true,six_unit_receipts:true,node_isolated:true};
}
