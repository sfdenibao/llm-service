'use strict';
const $ = id => document.getElementById(id);
let credential = '', tenants = [], selected = null, view = 'tenants', usageCursor = null, auditCursor = null;
let usageRows = [], auditRows = [], saveDialog = null;
const labels = {active:'正常',disabled:'已停用',revoked:'已撤销',completed:'已完成',running:'进行中',reserved:'已预占',cancelled:'已取消',timed_out:'超时',failed:'失败',interrupted:'中断恢复',pending_reconciliation:'待对账',rejected:'已拒绝'};
const usageLabels={exact:'精确结算',not_started:'未调用引擎',unknown:'待对账',unknown_released:'未精确计费，已释放预占'};
const el = (tag, value, cls) => { const n=document.createElement(tag); if(value!==undefined)n.textContent=value; if(cls)n.className=cls; return n; };
const money = value => value===null?'未知':(Number(value)/1e6).toFixed(6)+' USD';
function notice(text,error=false){$('notice').textContent=text;$('notice').className=error?'error':'';}
async function api(path,method='GET',data,extra={}){
 const response=await fetch(path,{method,headers:{'Authorization':'Bearer '+credential,'Content-Type':'application/json',...extra},...(data===undefined?{}:{body:JSON.stringify(data)})});
 const result=await response.json();
 if(!response.ok)throw new Error(result.error?.message||'请求失败 ('+response.status+')');
 return result;
}
async function safe(fn){try{await fn();}catch(error){notice(error.message,true);}}
function table(target,heads,rows){
 const root=$(target);root.replaceChildren();
 if(!rows.length){root.append(el('p','暂无记录','empty'));return;}
 const t=el('table'),header=el('tr');heads.forEach(h=>header.append(el('th',h)));t.append(el('thead'));t.firstChild.append(header);
 const body=el('tbody');rows.forEach(values=>{const row=el('tr');values.forEach(v=>{const cell=el('td');cell.append(v instanceof Node?v:document.createTextNode(v??'未知'));row.append(cell);});body.append(row);});t.append(body);root.append(t);
}
function action(text,fn,secondary=true){const b=el('button',text,secondary?'secondary':'');b.type='button';b.addEventListener('click',()=>safe(fn));return b;}
function status(value){return el('span',labels[value]||value,'status'+(['active','completed'].includes(value)?'':' off'));}
function field(name,label,type='text',value='',attrs={}){
 const wrap=el('label',label),input=el('input');input.name=name;input.type=type;input.value=value;input.required=true;
 Object.entries(attrs).forEach(([k,v])=>input.setAttribute(k,v));wrap.append(input);return wrap;
}
function dialog(title,fields,save){$('dialog-title').textContent=title;$('dialog-fields').replaceChildren(...fields);$('dialog-error').textContent='';saveDialog=save;$('edit-dialog').showModal();}
function showSecret(value){$('issued-secret').value=value;$('copy-status').textContent='';$('secret-dialog').showModal();}
async function loadTenants(){
 tenants=(await api('/admin/tenants')).items;
 table('tenant-table',['用户','状态','余额','预占','已用 / 上限 tokens','RPM / 并发','操作'],tenants.map(t=>[
 t.name,status(t.status),money(t.balance_micro_usd),money(t.reserved_micro_usd),`${t.used_tokens} / ${t.token_limit}`,`${t.rpm_limit} / ${t.concurrency_limit}`,
 action('管理 →',async()=>{selected=t;await loadKeys();})]));
 const current=$('usage-tenant').value;$('usage-tenant').replaceChildren(new Option('请选择用户',''),...tenants.map(t=>new Option(t.name,t.tenant_id)));
 $('usage-tenant').value=tenants.some(t=>t.tenant_id===current)?current:'';
 if(selected){selected=tenants.find(t=>t.tenant_id===selected.tenant_id)||null;if(selected)await loadKeys();}
}
async function loadKeys(){
 $('tenant-detail').hidden=!selected;if(!selected)return;
 $('detail-title').textContent=selected.name+' · 密钥';$('toggle-tenant').textContent=selected.status==='active'?'停用用户':'启用用户';
 const data=await api('/admin/tenants/'+selected.tenant_id+'/keys');
 table('key-table',['名称','凭据摘要','状态','到期时间','操作'],data.items.map(k=>{
  const actions=el('div');
  if(k.status==='active'){
   actions.append(action('轮换',async()=>{const next=await api('/admin/keys/'+k.key_id+'/rotate','POST',{});await loadKeys();showSecret(next.api_key);notice('已轮换；旧 key 的新请求立即失效。');}),
     action('撤销',async()=>{await api('/admin/keys/'+k.key_id+'/revoke','POST',{});await loadKeys();notice('密钥已撤销。');}));
  }
  return [k.name,el('code',k.masked_key),status(k.status),k.expires_at?new Date(k.expires_at).toLocaleString():'长期有效',actions];
 }));
}
async function loadUsage(more=false){
 const tid=$('usage-tenant').value;if(!tid){$('usage-summary').replaceChildren();table('usage-table',[],[]);return;}
 const data=await api('/admin/usage?tenant_id='+encodeURIComponent(tid)+(more&&usageCursor?'&cursor='+usageCursor:''));
 usageRows=more?[...usageRows,...data.items]:data.items;usageCursor=data.next_cursor;$('more-usage').hidden=usageCursor===null;
 const s=data.summary;$('usage-summary').replaceChildren(...[['请求数',s.request_count],['输入 tokens',s.input_tokens],['输出 tokens',s.output_tokens],['累计费用',money(s.cost_micro_usd)],['可用余额',money(s.balance_micro_usd-s.reserved_micro_usd)]].map(([title,value])=>{const d=el('div');d.append(el('span',title),el('strong',value));return d;}));
 table('usage-table',['时间 / 请求','路由','状态','输入 / 输出','费用','计费状态','价格版本'],usageRows.map(r=>[
  el('code',new Date(r.created_at).toLocaleString()+' · '+r.request_id.slice(-8)),r.route,status(r.status),`${r.input_tokens??'未知'} / ${r.output_tokens??'未知'}`,
  money(r.cost_micro_usd),usageLabels[r.usage_state]||r.usage_state,el('code',r.pricing_version.slice(-10))]));
}
async function loadPricing(){const p=await api('/admin/pricing');$('price-model').value=p.model;$('input-price').value=p.input_micro_usd_per_token;$('output-price').value=p.output_micro_usd_per_token;$('price-version').textContent='当前版本 '+p.pricing_version;}
async function loadAudit(more=false){const data=await api('/admin/audit'+(more&&auditCursor?'?cursor='+auditCursor:''));auditRows=more?[...auditRows,...data.items]:data.items;auditCursor=data.next_cursor;$('more-audit').hidden=auditCursor===null;table('audit-table',['时间','事件','用户','资源','详情'],auditRows.map(r=>[new Date(r.created_at).toLocaleString(),r.event,el('code',r.tenant_id?.slice(-10)||'—'),el('code',r.resource_id?.slice(-10)||'—'),JSON.stringify(r.detail)]));}
async function refresh(){if(view==='tenants')await loadTenants();if(view==='usage'){await loadTenants();await loadUsage();}if(view==='pricing')await loadPricing();if(view==='audit')await loadAudit();const r=await fetch('/healthz');$('health').textContent=r.ok?'● 服务就绪':'○ 引擎尚未就绪';}
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>safe(async()=>{view=b.dataset.view;document.querySelectorAll('[data-view]').forEach(n=>n.classList.toggle('active',n===b));['tenants','usage','pricing','audit'].forEach(v=>$('view-'+v).hidden=v!==view);$('view-title').textContent=b.textContent;notice('');await refresh();})));
$('login-form').addEventListener('submit',async event=>{event.preventDefault();credential=$('admin-key').value;$('login-error').textContent='';try{await loadTenants();$('admin-key').value='';$('login').hidden=true;$('workspace').hidden=false;await refresh();}catch(error){credential='';$('login-error').textContent=error.message;}});
$('logout').addEventListener('click',()=>location.reload());$('refresh').addEventListener('click',()=>safe(refresh));
$('usage-tenant').addEventListener('change',()=>safe(()=>loadUsage()));$('more-usage').addEventListener('click',()=>safe(()=>loadUsage(true)));$('more-audit').addEventListener('click',()=>safe(()=>loadAudit(true)));
$('new-tenant').addEventListener('click',()=>dialog('创建用户',[
 field('name','名称','text','',{maxlength:120}),field('balance_micro_usd','实验余额 / micro-USD','number',10000,{min:0}),
 field('token_limit','累计 token 上限','number',100000,{min:0}),field('rpm_limit','每分钟请求数上限','number',60,{min:1,max:100000}),field('concurrency_limit','并发上限','number',4,{min:1,max:64})],async f=>{
 const data=Object.fromEntries(f);for(const k of ['balance_micro_usd','token_limit','rpm_limit','concurrency_limit'])data[k]=Number(data[k]);selected=await api('/admin/tenants','POST',data);await loadTenants();notice('用户已创建。');}));
$('edit-quota').addEventListener('click',()=>dialog('编辑 '+selected.name+' 的额度',[
 field('token_limit','累计 token 上限','number',selected.token_limit,{min:0}),field('rpm_limit','RPM 上限','number',selected.rpm_limit,{min:1,max:100000}),field('concurrency_limit','并发上限','number',selected.concurrency_limit,{min:1,max:64})],async f=>{const data=Object.fromEntries(f);Object.keys(data).forEach(k=>data[k]=Number(data[k]));await api('/admin/tenants/'+selected.tenant_id,'PATCH',data);await loadTenants();notice('用户级额度已更新，多把 key 共享限额。');}));
$('add-credit').addEventListener('click',()=>{const id=crypto.randomUUID();dialog('调整实验余额',[field('delta_micro_usd','调整金额 / micro-USD（可为负数）','number',10000),field('reason','调整原因','text','实验额度调整',{maxlength:200})],async f=>{await api('/admin/tenants/'+selected.tenant_id+'/credits','POST',{delta_micro_usd:Number(f.get('delta_micro_usd')),reason:f.get('reason')},{'Idempotency-Key':id});await loadTenants();notice('余额调整已入账。');});});
$('toggle-tenant').addEventListener('click',()=>safe(async()=>{await api('/admin/tenants/'+selected.tenant_id,'PATCH',{status:selected.status==='active'?'disabled':'active'});await loadTenants();notice('用户状态已更新。');}));
$('new-key').addEventListener('click',()=>{const expiry=field('expires_at','到期时间（可留空）','datetime-local');expiry.querySelector('input').required=false;dialog('签发调用密钥',[field('name','密钥名称','text','Agent key',{maxlength:120}),expiry],async f=>{const expires=f.get('expires_at');const result=await api('/admin/tenants/'+selected.tenant_id+'/keys','POST',{name:f.get('name'),expires_at:expires?new Date(expires).toISOString():null});await loadKeys();showSecret(result.api_key);});});
$('edit-form').addEventListener('submit',async event=>{event.preventDefault();$('dialog-submit').disabled=true;try{await saveDialog(new FormData(event.currentTarget));$('edit-dialog').close();}catch(error){$('dialog-error').textContent=error.message;}finally{$('dialog-submit').disabled=false;}});
$('close-dialog').addEventListener('click',()=>$('edit-dialog').close());
$('price-form').addEventListener('submit',event=>{event.preventDefault();safe(async()=>{await api('/admin/pricing','PUT',{model:$('price-model').value,input_micro_usd_per_token:Number($('input-price').value),output_micro_usd_per_token:Number($('output-price').value)});await loadPricing();notice('新价格已生效，历史账目保持原价格。');});});
$('copy-secret').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('issued-secret').value);$('copy-status').textContent='已复制。';}catch{$('copy-status').textContent='请手动选择并复制密钥。';}});
$('close-secret').addEventListener('click',()=>$('secret-dialog').close());$('secret-dialog').addEventListener('close',()=>{$('issued-secret').value='';$('copy-status').textContent='';});
