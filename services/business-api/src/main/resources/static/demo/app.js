"use strict";
const $ = id => document.getElementById(id);
const names = {paid_amount:"已支付订单金额",paid_order_count:"已支付订单数",refund_amount:"成功退款金额",event_net:"期间支付减退款",cohort_net:"订单批次净额",cohort_refunded_order_count:"订单批次退款订单数",paid_amount_for_product_orders:"含指定商品的订单支付总额",product_refund_amount:"商品退款金额"};
const definitions = {paid_amount:"期间内已支付订单金额，不扣退款。",paid_order_count:"按订单去重，统计期间内已支付订单数。",refund_amount:"按退款发生时间统计期间成功退款。",event_net:"期间支付额减期间成功退款；退款可能来自更早的订单。",cohort_net:"选定期间支付的订单，减去这批订单在截止时间前的成功退款。",cohort_refunded_order_count:"选定期间支付的订单中，截止时间前有成功退款的订单数。",paid_amount_for_product_orders:"包含该商品的整张订单金额，不等于该商品销售额。",product_refund_amount:"仅当退款具备完整商品归属时可计算。样例中部分退款缺少归属。"};
const statusNames = {QUEUED:"排队中",RUNNING:"执行中",SUCCEEDED:"已完成",FAILED:"未完成",CANCELLED:"已取消"};
const issues = {none:"已回答",needs_time_range:"需要明确时间",needs_metric_definition:"需要明确口径",needs_refund_cutoff:"需要退款截止时间",missing_data:"数据不足",unsupported_request:"超出分析范围"};
const errors = {model_authentication_failed:"模型服务鉴权失败，请在个人终端检查配置；未自动重试。",model_balance_insufficient:"模型账户余额不足，任务已停止。",model_request_invalid:"模型请求参数被拒绝，请由部署者检查运行记录。",model_rate_limited:"模型服务限流，任务已停止；请先核对费用记录。",model_provider_unavailable:"模型服务暂不可用，任务已停止；请先核对费用记录。",model_connection_failed:"未收到有效模型响应，请检查网络并核对费用记录。",model_response_unknown:"模型响应无法确认，未知费用预占已保留。",model_failure_requires_review:"此前模型请求结果未确认，需要核对后才能继续。",session_request_rejected:"页面会话已变化，请刷新后重试。",task_active_quota_exceeded:"排队和执行中的任务已达上限，请等待完成或取消不需要的任务。",task_retention_quota_exceeded:"历史任务已达到保留上限，请由部署者备份并维护存储。",task_deadline_exceeded:"任务已超过最长等待与执行时间，请检查 Worker 状态后重新提交。",executor_output_limit:"执行输出超过限额，任务已停止，请查看运行记录。",live_agent_disabled:"实时 Agent 尚未开启。请先在个人终端运行带 --live 的启动命令，或查看下方历史样例。",same_origin_required:"请从本机 Demo 地址打开页面。",invalid_period:"结束日期必须晚于开始日期。",personal_budget_exhausted:"个人模型预算不足，任务已停止。",model_pricing_needs_revalidation:"模型价格日期需要重新核验，暂不发起调用。",personal_model_not_configured:"尚未配置个人模型。",executor_failed:"分析执行失败；可以在本地任务日志中核查。",task_not_found:"找不到这项任务，请从任务记录重新选择。",product_refund_attribution_incomplete:"退款缺少完整的商品归属，当前不能计算商品退款金额。"};
const SALES_CHANGE="sales_change_by_product", REFUND_CHANGE="refund_change", RECONCILIATION="report_reconciliation";
names[RECONCILIATION]="报表差异核对";
definitions[RECONCILIATION]="核对两份报告的声明口径与授权账本重算值，保留未解释偏差。";
let reportClaims={left:null,right:null}, reportLoads={left:0,right:0};
const isComparison=metric=>[SALES_CHANGE,REFUND_CHANGE].includes(metric);
names[REFUND_CHANGE]="退款变化定位";
definitions[REFUND_CHANGE]="按成功退款发生期比较等长两期，区分原订单支付时间、已关联商品与缺失归属；不推断退款原因。";
errors.refund_change_rejected="退款变化分析未通过检查，请确认退款数据、快照时间与两期范围。";
names[SALES_CHANGE]="销售变化的商品贡献";
definitions[SALES_CHANGE]="以已支付金额、不扣退款为口径，按商品分摊金额比较两期。两期必须等长、先后且不重叠。";
Object.assign(errors,{incomparable_periods:"两期必须等长、先后且不重叠，请核对基期和对比期日期。",invalid_top_n:"每个方向展示 1 至 10 个商品。",sales_change_rejected:"商品变化分析未通过数据或期间检查，请核对日期是否超过快照截至时间，以及两期口径。"});
let config, mode="metric", activeTask=null, generation=0, pollTimer=null, nextCursor=null, submitting=false, pendingSubmission=null, samples=[], datasets=new Map(), datasetAvailable=true;
const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
function notify(message){$("notice").textContent=message||"";$("notice").hidden=!message;}
function errorMessage(error){if(error.message==="already_terminal")return "任务刚刚结束，已不能取消。请查看最终结果。";return errors[error.message]||`请求未完成：${error.message}。请检查服务是否仍在运行。`;}
async function api(path,body,key){
  const headers={"X-Commerce-Demo":"1",...window.commerceSessionHeaders()};if(body!==undefined)headers["Content-Type"]="application/json";if(key)headers["Idempotency-Key"]=key;
  const response=await fetch("/demo/api"+path,{method:body===undefined?"GET":"POST",headers,body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(10000)});
  if(response.status===401){location.assign("/login?expired");throw new Error("login_required");}const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${response.status}`);return data;
}
function setMode(value){mode=value;$("metric-fields").hidden=mode!=="metric";$("agent-fields").hidden=mode!=="agent";$("reconcile-fields").hidden=mode!=="reconcile";$("report-confirm").required=mode==="reconcile";$("time-note").textContent=mode==="reconcile"?"按报告声明的时区与边界核对":"日期按 UTC 00:00 计算";for(const kind of ["metric","agent","reconcile"]){$(kind+"-tab").classList.toggle("selected",kind===mode);$(kind+"-tab").setAttribute("aria-pressed",String(kind===mode));}
  $("start").required=$("end").required=mode==="metric";$("question").required=mode==="agent";updateFields();
  const selected=datasets.get($("dataset-select").value);
  $("submit").disabled=submitting||!config||!datasetAvailable||(mode==="reconcile"&&(!reportClaims.left||!reportClaims.right||!$("report-confirm").checked))||(mode==="agent"&&(!config.agent_enabled||(selected&&!selected.model_allowed)));
}
function updateFields(){const metric=$("metric").value;const cohort=metric.startsWith("cohort_");const product=["paid_amount_for_product_orders","product_refund_amount"].includes(metric);const sales=isComparison(metric);$("top-n-label").textContent=metric===REFUND_CHANGE?"已关联商品显示":"每个方向显示";$("cutoff-field").hidden=!cohort;$("product-field").hidden=!product;$("comparison-fields").hidden=!sales;$("current-start").required=$("current-end").required=mode==="metric"&&sales;$("cutoff").required=mode==="metric"&&cohort;$("product").required=mode==="metric"&&product;$("metric-help").textContent=definitions[metric];$("baseline-label").textContent=sales?"基期":"分析期间";}
function applyDataset(id){
  const d=datasets.get(id);datasetAvailable=id==="demo-ledger-v1"||!!d;
  if(!datasetAvailable){
    if(![...$("dataset-select").options].some(o=>o.value===id)){const o=node("option","历史数据版本（当前不可用）");o.value=id;o.disabled=true;$("dataset-select").append(o);}
    $("dataset-select").value=id;$("context-name").textContent="历史数据版本（当前不可用）";$("context-version").textContent=id;$("context-currency").textContent=$("context-export").textContent="—";$("context-boundary").textContent="此版本可能已归档。旧报告保留；新分析须明确选择另一个可用版本。";
    notify("该数据版本不在可用列表中，可能已归档。历史报告仍可查看；重新分析前请选择可用版本。");setMode(mode);return;
  }
  if($("notice").textContent.startsWith("该数据版本不在可用列表"))notify("");
  $("dataset-select").value=id;
  for(const option of $("metric").options)option.disabled=!!d&&!d.available_metrics.includes(option.value===SALES_CHANGE?"paid_amount":option.value===REFUND_CHANGE?"refund_amount":option.value);
  if($("metric").selectedOptions[0]?.disabled)$("metric").value=d.available_metrics[0];
  $("context-name").textContent=d?d.name:"合成电商账本";
  $("context-version").textContent=id;$("context-currency").textContent=(d?d.currency:"CNY")+" / 分";
  $("context-export").textContent=d?d.exported_at:"2026-10-26T00:00:00Z";
  $("context-boundary").textContent=d?`已确认修订 ${d.revision}，开放 ${d.available_metrics.length} 项指标。缺失数据对应指标已限制；没有成本或流量数据，不能计算利润或证明原因。`:"可以核查支付、退款与订单批次。缺少成本和流量，不能解释利润或证明变化原因。部分退款没有商品归属。";
  $("agent-note").textContent=d&&!d.model_allowed?"此数据版本仅允许本机计算，确认时没有允许模型使用。":config.agent_enabled?"实时模式已开启。提交后使用个人 DeepSeek API，与本地评测共用已设置的 5 元总预算上限。":"默认不调用模型。实时提问需在个人终端运行 scripts/web_demo.py --live；Key 不经过网页。历史样例基于内置合成账本。";
  setMode(mode);
}
async function loadDatasets(){const response=await api("/datasets");for(const d of response.items){datasets.set(d.id,d);const option=node("option",d.name+" · 修订 "+d.revision);option.value=d.id;$("dataset-select").append(option);}applyDataset("demo-ledger-v1");}
$("dataset-select").onchange=()=>{$("report-confirm").checked=false;applyDataset($("dataset-select").value);for(const side of ["left","right"])previewClaim(side);location.hash="dataset="+encodeURIComponent($("dataset-select").value);};
function amount(result,value=result.value){
  if(!Number.isSafeInteger(value))return "数值超出展示精度，请查看原始报告";
  if(result.result_unit!=="minor_units")return value.toLocaleString("zh-CN")+" 单";
  // Formatting only. All calculations and comparisons come from the published report.
  const scale=result.amount_scale, digits=String(Math.abs(value)).padStart(scale+1,"0");
  return (value<0?"−":"")+(scale?digits.slice(0,-scale)+"."+digits.slice(-scale):digits)+" "+result.currency;
}
function period(result){return `${result.period.start.slice(0,10)} → ${result.period.end.slice(0,10)}（不含）`;}
function title(task){return task.request.question||names[task.request.metric]||"指标查询";}
function badge(status){return node("span",statusNames[status]||status,"status "+({FAILED:"failed",RUNNING:"running",QUEUED:"running",CANCELLED:"cancelled"}[status]||""));}
function details(titleText,content){const d=node("details");d.append(node("summary",titleText),content);return d;}
function action(text,handler){const b=node("button",text,"secondary");b.type="button";b.onclick=handler;return b;}
function download(task,historical){const blob=new Blob([JSON.stringify({display_source:historical?"historical_live_report":"current_task",task},null,2)],{type:"application/json"});const url=URL.createObjectURL(blob);const a=node("a");a.href=url;a.download=`commerce-${historical?"historical-":""}${task.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function metricNumber(result, value) {
  const text=amount(result,value), suffix=result.result_unit==="minor_units"?result.currency:"单";
  const number=node("div",undefined,"number");
  if(Number.isSafeInteger(value)){number.append(document.createTextNode(text.slice(0,-suffix.length).trim()),node("small",suffix));}
  else number.textContent=text;
  return number;
}
function setView(view) {
  const pages={analysis:["经营分析","比较销售变化，核查订单与退款。"],history:["任务记录","查看已保存的分析，恢复条件并核查原始报告。"],samples:["Agent 报告样例","了解自然语言分析的结果、澄清与能力边界。"]};
  const selected=pages[view]?view:"analysis";
  $("analysis-view").hidden=selected!=="analysis";$("history").hidden=selected!=="history";$("samples").hidden=selected!=="samples";
  $("page-title").textContent=$("breadcrumb").textContent=pages[selected][0];$("page-description").textContent=pages[selected][1];
  document.title=pages[selected][0]+" · Commerce Insight";
  for(const link of document.querySelectorAll("nav [data-view]")){const active=link.dataset.view===selected;link.classList.toggle("nav-active",active);if(active)link.setAttribute("aria-current","page");else link.removeAttribute("aria-current");}
}
function restoreRequest(task) {
  applyDataset(task.request.dataset_id||"demo-ledger-v1");
  const q=task.request;
  if(q.metric===RECONCILIATION){reportClaims={left:q.left,right:q.right};for(const side of ["left","right"]){reportLoads[side]++;$("report-"+side+"-file").value="";previewClaim(side);}$("report-confirm").checked=false;setMode("reconcile");}
  else if(q.kind==="agent"){$("question").value=q.question;setMode("agent");}
  else {
    $("metric").value=q.metric;
    for(const [field,key] of [["start","start"],["end","end"],["cutoff","as_of"],["current-start","current_start"],["current-end","current_end"]])if(q[key])$(field).value=q[key].slice(0,10);
    if(q.product_id)$("product").value=q.product_id;
    if(q.top_n){if(![...$("top-n").options].some(o=>o.value===String(q.top_n))){const o=node("option",q.top_n+" 个商品");o.value=String(q.top_n);$("top-n").append(o);}$("top-n").value=String(q.top_n);}
    setMode("metric");
  }
  if(window.matchMedia("(max-width:1023px)").matches)$("query-panel").open=false;
}
function salesChangeView(r){
  const block=node("div",undefined,"sales-change");
  block.append(node("h3","已支付金额 · 不扣退款"),node("p",`基期 ${period(r)}；对比期 ${r.period.current_start.slice(0,10)} → ${r.period.current_end.slice(0,10)}（不含）`,"period-caption"));
  const totals=node("div",undefined,"result-cards");
  for(const [label,key] of [["基期金额","baseline_minor"],["对比期金额","current_minor"],["变化金额","delta_minor"]]){
    const card=node("div",undefined,"result-card"),num=metricNumber(r,r.totals[key]);
    if(key==="delta_minor")num.classList.add(r.totals[key]<0?"negative":r.totals[key]>0?"positive":"neutral");
    card.append(node("label",label),num);totals.append(card);
  }
  block.append(totals);
  const context=node("div",undefined,"change-context");
  context.append(node("strong",r.totals.change_percent===null?"基期为零，不计算变化率":`变化率 ${r.totals.change_percent}%`,r.totals.delta_minor<0?"negative":""),node("span",`${r.product_count} 个期间有额商品，${r.unchanged_count} 个金额不变`));block.append(context);
  const chart=node("section",undefined,"contribution-plot");chart.setAttribute("aria-label","商品金额变化图");chart.append(node("h3","变化集中在哪些商品"));
  const legend=node("div",undefined,"chart-legend");legend.append(node("span","下降"),node("span","增长"));chart.append(legend);
  const rows=[];
  for(const key of ["decreases","increases"]){const group=r[key];for(const row of group.products)rows.push({...row,direction:key});if(group.other.product_count)rows.push({product_id:`其他 ${group.other.product_count} 个${key==="decreases"?"下降":"增长"}商品`,delta_minor:group.other.delta_minor,direction:key});}
  const max=Math.max(1,...rows.map(row=>Math.abs(row.delta_minor)));
  for(const row of rows){const line=node("div",undefined,"contribution-row "+(row.direction==="decreases"?"negative":"positive")),meter=node("meter");meter.min=0;meter.max=max;meter.value=Math.abs(row.delta_minor);meter.setAttribute("aria-label",`${row.product_id} ${row.direction==="decreases"?"下降":"增长"} ${amount(r,Math.abs(row.delta_minor))}`);line.append(node("span",row.product_id),meter,node("b",(row.delta_minor>0?"+":"")+amount(r,row.delta_minor)));chart.append(line);}
  if(!rows.length)chart.append(node("p","两个期间没有金额变化的商品。","field-note"));
  chart.append(node("p","条长表示变化金额的绝对值，增长与下降使用相同刻度。","field-note"));block.append(chart);
  for(const [key,label] of [["decreases","下降"],["increases","增长"]]){const group=r[key];block.append(node("h3",`${label}商品 · 合计变化 ${amount(r,group.delta_minor)}`));if(!group.product_count){block.append(node("p",`没有${label}商品。`,"field-note"));continue;}
    const wrap=node("div",undefined,"contribution-scroll");wrap.tabIndex=0;wrap.setAttribute("role","region");wrap.setAttribute("aria-label",label+"商品明细，可横向滚动");
    const table=node("table",undefined,"contribution-table"),head=node("tr");
    for(const heading of ["商品编号","基期","对比期","变化金额",`占全部${label}`]){const th=node("th",heading);th.scope="col";head.append(th);}
    const thead=node("thead");thead.append(head);table.append(thead);const body=node("tbody");
    for(const row of group.products){const tr=node("tr");[row.product_id,amount(r,row.baseline_minor),amount(r,row.current_minor),amount(r,row.delta_minor),row.direction_share_percent+"%"].forEach((text,i)=>tr.append(node("td",text,i===3?(row.delta_minor<0?"negative":"positive"):undefined)));body.append(tr);}
    if(group.other.product_count){const tr=node("tr");for(const text of [`其他 ${group.other.product_count} 个商品`,"—","—",amount(r,group.other.delta_minor),"—"])tr.append(node("td",text));body.append(tr);}table.append(body);wrap.append(table);block.append(wrap);
  }
  block.append(node("p","占比按增长、下降方向分别计算，其他商品也计入总额。零基数不计算增长率；期间无额不代表上架或下架。以上定位变化集中在哪里，不证明经营原因。","field-note"));
  block.append(details("核对商品金额与订单总额",node("pre",JSON.stringify(r.reconciliation,null,2))));return block;
}
function refundChangeView(r){
  const block=node("div",undefined,"sales-change");
  block.append(node("h3","成功退款 · 按退款发生时间"),node("p",`基期 ${period(r)}；对比期 ${r.period.current_start.slice(0,10)} → ${r.period.current_end.slice(0,10)}（不含）`,"period-caption"));
  const totals=node("div",undefined,"result-cards");
  for(const [label,key] of [["基期退款","baseline_minor"],["对比期退款","current_minor"],["变化金额","delta_minor"]]){const card=node("div",undefined,"result-card");card.append(node("label",label),metricNumber(r,r.totals[key]));totals.append(card);}block.append(totals);
  const c=r.counts,a=r.attribution;
  const context=node("div",undefined,"change-context");context.append(node("strong",r.totals.change_percent===null?"基期为零，不计算变化率":`金额变化率 ${r.totals.change_percent}%`),node("span",`退款 ${c.baseline_refunds} → ${c.current_refunds} 笔 · 去重订单 ${c.baseline_orders} → ${c.current_orders} 单`));block.append(context);
  function table(label,rows){
    block.append(node("h3",label));const wrap=node("div",undefined,"contribution-scroll");wrap.tabIndex=0;wrap.setAttribute("role","region");wrap.setAttribute("aria-label",label+"，可横向滚动");
    const t=node("table",undefined,"contribution-table"),head=node("tr"),thead=node("thead"),body=node("tbody");
    for(const text of ["归属","基期","对比期","变化金额"]){const th=node("th",text);th.scope="col";head.append(th);}thead.append(head);t.append(thead);
    for(const row of rows){const tr=node("tr");for(const text of [row.label,amount(r,row.baseline_minor),amount(r,row.current_minor),amount(r,row.delta_minor)])tr.append(node("td",text));body.append(tr);}t.append(body);wrap.append(t);block.append(wrap);
  }
  table("原订单在什么时候支付",r.origins.map(row=>({...row,label:row.origin==="prior_orders"?"各期开始前支付":"各期内支付"})));
  const coverage=value=>value===null?"无退款":value+"%";
  block.append(node("h3","商品归属完整度"),node("p",`金额覆盖率 ${coverage(a.baseline_coverage_percent)} → ${coverage(a.current_coverage_percent)}。未归属金额单列，不按订单比例推测分摊。`,"field-note"));
  const unknown={label:"未关联商品",baseline_minor:a.baseline_unattributed_minor,current_minor:a.current_unattributed_minor,delta_minor:a.delta_unattributed_minor};
  table("缺失归属的退款",[unknown]);
  const rows=r.products.rows.map(row=>({...row,label:row.product_id}));
  if(r.products.other.product_count)rows.push({...r.products.other,label:`其他 ${r.products.other.product_count} 个已关联商品`});
  if(rows.length)table("已关联商品的变化明细",rows);else block.append(node("p","两期内没有可关联到商品的成功退款。","field-note"));
  block.append(node("p","商品按变化金额绝对值排序。归属不完整时，这不是全体商品的完整排名；退款来自不同支付批次，不能据此计算本期订单退款率或判断原因。","field-note"));
  block.append(details("核对分项与成功退款总额",node("pre",JSON.stringify(r.reconciliation,null,2))));return block;
}
function readReportClaim(raw){
  if(!raw||typeof raw!=="object"||Array.isArray(raw))throw new Error("invalid_report_claim");
  // The actual download format wraps the immutable task with its display provenance.
  if(["current_task","historical_live_report"].includes(raw.display_source)&&raw.task)raw=raw.task;
  let claim=raw;
  if(raw.status==="SUCCEEDED"&&raw.request&&raw.result&&Number.isSafeInteger(raw.result.value)){
    const query={};for(const key of ["metric","start","end","as_of","product_id"])if(raw.request[key]!==undefined)query[key]=raw.request[key];
    claim={schema_version:"commerce-report-claim-v1",name:names[query.metric]||query.metric,query,reported_value:raw.result.value,currency:raw.result.currency,amount_scale:raw.result.amount_scale,result_unit:raw.result.result_unit,metric_version:raw.result.metric_version,source_sha256:raw.result.evidence?.source_sha256??null};
  }
  const fields=["schema_version","name","query","reported_value","currency","amount_scale","result_unit","metric_version","source_sha256"];
  const metrics=["paid_amount","paid_order_count","refund_amount","event_net","cohort_net","cohort_refunded_order_count","paid_amount_for_product_orders","product_refund_amount"];
  if(Object.keys(claim).length!==fields.length||fields.some(k=>!Object.hasOwn(claim,k))||claim.schema_version!=="commerce-report-claim-v1"||!claim.query||!metrics.includes(claim.query.metric))throw new Error("invalid_report_claim");
  if(!Number.isSafeInteger(claim.reported_value)||!Number.isInteger(claim.amount_scale)||claim.amount_scale<0||claim.amount_scale>6||!["minor_units","orders"].includes(claim.result_unit))throw new Error("invalid_report_value");
  for(const key of ["name","currency","metric_version"])if(typeof claim[key]!=="string"||!claim[key].trim()||claim[key].length>160)throw new Error("invalid_report_claim");
  if(claim.source_sha256!==null&&(typeof claim.source_sha256!=="string"||!/^[a-f0-9]{64}$/.test(claim.source_sha256)))throw new Error("invalid_report_source");
  for(const key of ["start","end"])if(typeof claim.query[key]!=="string"||claim.query[key].length>40||!Number.isFinite(Date.parse(claim.query[key])))throw new Error("invalid_report_period");
  return claim;
}
function reportTime(value){return value.replace("T"," ").replace(/\.0+(?=Z$|[+-]\d\d:\d\d$)/,"").replace(/Z$|\+00:00$/," UTC");}
function previewClaim(side){
  const c=reportClaims[side],box=$("report-"+side+"-preview");box.replaceChildren();
  if(!c){box.textContent="尚未选择";return;}
  box.append(node("strong","已载入 · "+c.name),node("div",`${names[c.query.metric]} · 声明 ${amount(c,c.reported_value)}`),node("div",`${reportTime(c.query.start)} → ${reportTime(c.query.end)}（结束不含）`));
  if(c.query.as_of)box.append(node("div","退款截至（不含）"+reportTime(c.query.as_of)));
  if(c.query.product_id)box.append(node("div","商品范围："+c.query.product_id));
  const selected=datasets.get($("dataset-select").value),source=$("dataset-select").value==="demo-ledger-v1"?config.source_sha256:selected?.source_sha256;
  box.append(node("div",!c.source_sha256?"原始数据版本未知，只能提供参考计算":c.source_sha256===source?"报告声明的源版本与所选账本一致":"报告声明的源版本与所选账本不同"));
  if(c.source_sha256)box.append(details("源版本标识",node("div",c.source_sha256)));
}
for(const side of ["left","right"]){$("report-"+side+"-file").onchange=async event=>{
  const load=++reportLoads[side];reportClaims[side]=null;$("report-confirm").checked=false;previewClaim(side);setMode(mode);notify("");
  const file=event.target.files[0];if(!file)return;
  try{if(file.size>100000)throw new Error("report_file_too_large");const claim=readReportClaim(JSON.parse(await file.text()));if(load!==reportLoads[side])return;reportClaims[side]=claim;previewClaim(side);}catch(error){if(load!==reportLoads[side])return;notify(errors[error.message]||"无法读取报告。请使用声明模板或本系统成功的单指标报告 JSON。");}finally{setMode(mode);}
};}
$("reconcile-example").onclick=()=>{
  applyDataset("demo-ledger-v1");$("report-confirm").checked=false;
  const left={schema_version:"commerce-report-claim-v1",name:"已支付金额报表",query:{metric:"paid_amount",start:"2026-10-05T00:00:00Z",end:"2026-10-12T00:00:00Z"},reported_value:28000,currency:"CNY",amount_scale:2,result_unit:"minor_units",metric_version:"ledger-metrics-v1",source_sha256:config.source_sha256};
  reportClaims={left,right:{...left,name:"净额报表（含 10 元待核查偏差）",query:{...left.query,metric:"cohort_net",as_of:"2026-10-19T00:00:00Z"},reported_value:24000}};
  for(const side of ["left","right"]){reportLoads[side]++;$("report-"+side+"-file").value="";previewClaim(side);}setMode("reconcile");notify("");
};
Object.assign(errors,{invalid_report_claim:"报告格式不受支持。请使用声明模板或本系统的成功单指标报告 JSON。",invalid_report_value:"声明值需为浏览器可精确表示的整数，金额须使用明确的最小单位。",invalid_report_source:"源数据 SHA 格式不正确；未知版本请在模板中填写 null。",report_file_too_large:"每份报告不得超过 100 KB。",report_confirmation_required:"请核对报告预览与所选账本后勾选确认。",report_reconciliation_rejected:"报表核对未通过输入检查，请检查声明格式和口径。"});
function reconciliationView(r){
  const block=node("div",undefined,"sales-change reconcile-report");
  const statuses={matched:"口径与金额一致",explained:"口径差异已解释",discrepancy:"仍有报表偏差待核查",needs_review:"证据或可比条件不足"};
  block.append(node("h3",statuses[r.status],"reconcile-summary"),node("p","分别核查声明值与账本重算值，再解释两份报告之间的差额。","field-note"));
  const issues={source_unknown:"原始数据版本未知",source_mismatch:"原始数据版本不一致",metric_version_mismatch:"指标版本不受支持",currency_mismatch:"币种不一致",amount_scale_mismatch:"金额单位精度不一致",result_unit_mismatch:"金额与订单数单位不符",calculation_unavailable:"数据或查询条件不足"};
  for(const [side,label] of [["left","左报告"],["right","右报告"]]){
    const report=r.reports[side],c=report.claim;block.append(node("h3",`${label} · ${c.name}`),node("p",names[c.query.metric]+" · "+reportTime(c.query.start)+" → "+reportTime(c.query.end),"field-note"));
    const cards=node("div",undefined,"result-cards");
    for(const [name,value,format] of [["报表声明",c.reported_value,c],[report.verified?"账本重算":"所选账本参考",report.recomputed_value,report.calculation],["声明减重算",report.residual_value,c]]){const card=node("div",undefined,"result-card");card.append(node("label",name),value===null?node("div","—","number"):metricNumber(format,value));cards.append(card);}block.append(cards);
    if(report.issues.length)block.append(node("p",report.issues.map(code=>issues[code]||code).join("；")+"。参考值不等于该报表已验证。","notice"));
    if(report.calculation_error)block.append(node("p","计算未完成："+report.calculation_error,"field-note"));
  }
  if(r.bridge){const bridge=node("div",undefined,"reconcile-bridge"),format=r.reports.left.claim;bridge.append(node("h3","从左报告到右报告的金额桥接"));
    for(const [label,value] of [["左报告声明",r.bridge.start_value],...r.bridge.components.map(c=>[c.label,c.value]),["右报告声明",r.bridge.end_value]]){const row=node("p");row.append(node("span",label),node("strong",amount(format,value)));bridge.append(row);}bridge.append(node("p",r.status==="discrepancy"?"非零报表偏差仍待核查，不能将其归为业务原因。":"两份报告声明值均与当前账本重算一致。","field-note"));block.append(bridge);
  }else block.append(node("p","当前不提供金额桥接：先补齐来源、指标定义或可比单位。","field-note"));
  const labels={currency:"币种",amount_scale:"金额精度",result_unit:"结果单位",metric_version:"指标版本",source_sha256:"数据版本",'query.metric':"指标",'query.start':"开始时间",'query.end':"结束时间",'query.as_of':"退款截止",'query.product_id':"商品范围",definition:"指标定义",event_time:"事件时间",status_filter:"状态筛选",grain:"计算粒度",refund_policy:"退款规则"};
  const semanticNames={paid_at:"订单支付时间",refunded_at:"退款成功时间","paid_at + as_of":"订单支付时间及退款截止","paid_at + refunded_at":"支付与退款各自发生时间",paid:"已支付",succeeded:"退款成功","paid + succeeded":"已支付且只扣成功退款",order:"订单",distinct_order:"去重订单",refund:"退款记录",payment_and_refund:"支付与退款记录",order_cohort:"同批支付订单",order_containing_product:"包含指定商品的整单",refund_item:"关联商品的退款记录",minor_units:"金额最小单位",orders:"订单数"};
  function differenceText(field,value){if(value===null)return "未声明";if(field==="query.metric")return names[value]||value;if(["query.start","query.end","query.as_of"].includes(field))return reportTime(String(value));return semanticNames[value]||String(value).replaceAll("as_of","指定截止时间");}
  block.append(node("h3","口径差异"));
  if(!r.differences.length)block.append(node("p","两份报告使用相同的声明口径。","field-note"));
  else{const wrap=node("div",undefined,"contribution-scroll");wrap.tabIndex=0;wrap.setAttribute("role","region");wrap.setAttribute("aria-label","报告口径差异，可横向滚动");const table=node("table",undefined,"contribution-table"),thead=node("thead"),head=node("tr"),body=node("tbody");for(const text of ["核查项","左报告","右报告"]){const th=node("th",text);th.scope="col";head.append(th);}thead.append(head);table.append(thead);
    for(const d of r.differences){const row=node("tr");for(const text of [labels[d.field]||d.field,differenceText(d.field,d.left),differenceText(d.field,d.right)])row.append(node("td",text===null?"未声明":String(text)));body.append(row);}table.append(body);wrap.append(table);block.append(wrap);}
  block.append(details("完整核对结论",node("div",r.summary,"answer")));
  for(const [side,label] of [["left","左报告"],["right","右报告"]]){const report=r.reports[side],body=node("div");body.append(node("pre",JSON.stringify({claim:report.claim,claim_sha256:report.claim_sha256,issues:report.issues},null,2)));if(report.calculation)body.append(node("pre",report.calculation.sql),node("pre",JSON.stringify(report.calculation.params,null,2)));block.append(details(label+" · 声明、重算 SQL 与证据",body));}
  return block;
}
function renderTask(task,sample){
  const report=$("report");report.replaceChildren();
  const heading=node("div",undefined,"section-heading");heading.append(node("h2","分析结果"));
  const terminal=!["QUEUED","RUNNING"].includes(task.status);
  if(terminal)heading.append(action("下载报告 JSON",()=>download(task,!!sample)));
  report.append(heading);
  if(sample){const reasons=sample.errors.map(e=>e==="wrong_issue"?"拒答原因分类不符合预期":e);report.append(node("div",`历史真实模型报告 · 2026-11-06 · ${sample.passed?"该样例通过原评测":"该样例未通过原评测："+reasons.join("；")}。本次仅查看，不运行模型。`,"historical-banner"));}
  const head=node("div",undefined,"report-head");head.append(badge(task.status),node("span",task.request.kind==="agent"?"自然语言分析":"指标分析 · 无模型调用","provenance"));
  report.append(head,node("h3",title(task),"report-question"));
  report.append(node("p",`${task.dataset?task.dataset.name+" · 修订 "+task.dataset.revision:"内置合成账本"} · ${new Date(task.created_at).toLocaleString("zh-CN")}`,"provenance"));
  if(!terminal){
    report.append(node("p",task.status==="QUEUED"?"正在排队，分析结果将自动更新。":"正在分析，完成后将在这里展示结果。","pending"));
    const b=action("取消这次分析",async()=>{b.disabled=true;try{const t=await api("/tasks/"+task.id+"/cancel",{});if(activeTask===task.id){renderTask(t);refreshHistory().catch(e=>notify(errorMessage(e)));}}catch(e){notify(errorMessage(e));}finally{b.disabled=false;}});report.append(b);
  }else if(task.status==="FAILED")report.append(node("p",errors[task.error_code]||`未能完成分析：${task.error_code||"未知原因"}。请调整条件后重新提交。`,"notice"));
  else if(task.status==="CANCELLED")report.append(node("p","本次任务已取消，未发布分析结论。","pending"));
  const r=task.result;
  if(r?.metric===RECONCILIATION){report.append(reconciliationView(r));}
  else if(r){
    const isAgent=r.kind==="agent_report_v1",evidence=isAgent?r.evidence:[{query:task.request,result:r}];
    if(isAgent)report.append(node("p",issues[r.issue]||r.issue,"provenance"));
    const cards=node("div",undefined,"result-cards");
    for(const e of evidence){if(e.result.metric===REFUND_CHANGE){cards.append(refundChangeView(e.result));continue;}if(e.result.metric===SALES_CHANGE){cards.append(salesChangeView(e.result));continue;}const card=node("div",undefined,"result-card");card.append(node("label",names[e.result.metric]||e.result.metric),metricNumber(e.result,e.result.value),node("p",period(e.result)));if(e.result.period.as_of_exclusive)card.append(node("p","退款截至 "+e.result.period.as_of_exclusive.slice(0,10)+"（不含）"));cards.append(card);}
    if(evidence.length)report.append(cards);
    for(const c of r.comparisons||[]){const a=evidence.find(e=>e.evidence_id===c.baseline_id)?.result,b=evidence.find(e=>e.evidence_id===c.current_id)?.result;if(!a||!b)continue;
      const chart=node("div",undefined,"chart");chart.append(node("h3",`同口径比较 · 差额 ${amount(b,c.delta)} · 变化率 ${c.change_percent===null?"基期为零，不计算":c.change_percent+"%"}`));
      if(a.value>=0&&b.value>=0){const max=Math.max(a.value,b.value,1);for(const [label,value] of [["基准期",a],["对比期",b]]){const row=node("div",undefined,"bar-row"),meter=node("meter");meter.min=0;meter.max=max;meter.value=value.value;meter.setAttribute("aria-label",label+" "+amount(value));row.append(node("span",label),meter,node("b",amount(value)));chart.append(row);}}report.append(chart);
    }
    const answer=node("div",isAgent?r.answer:isComparison(r.metric)?r.summary:definitions[r.metric]+"\n按报告时间与快照计算；不等于利润、到账或因果解释。","answer");
    report.append(!isAgent&&isComparison(r.metric)?details("完整文字结论",answer):answer);
    for(const [i,e] of evidence.entries()){const body=node("div");body.append(node("p",definitions[e.result.metric],"evidence-meta"),node("p",`快照：${e.result.snapshot_id}\n源文件 SHA-256：${e.result.evidence.source_sha256}\n证据 ID：${e.evidence_id||"确定性任务结果"}`,"evidence-meta"));for(const q of e.result.queries||[{sql:e.result.sql,params:e.result.params}])body.append(node("pre",(q.role?q.role+"\n":"")+q.sql),node("pre","参数（依次绑定）：\n"+JSON.stringify(q.params,null,2)));report.append(details(`证据 ${i+1} · ${names[e.result.metric]} · 查看口径、SQL 与参数`,body));}
    if(r.usage)report.append(details("模型与工具用量",node("pre",JSON.stringify({model:r.provenance.model,mode:r.provenance.mode,...r.usage},null,2))));
  }
  const trace=node("div");trace.append(node("p",`任务 ${task.id} · 尝试 ${task.attempt}/${task.max_attempts}`,"provenance"));
  if(task.dataset)trace.append(node("p",`数据版本 ${task.dataset.id} · 映射 ${task.dataset.mapping_sha256}`,"provenance"));
  const events=node("div",undefined,"events"),eventNames={CREATED:"创建任务",CLAIMED:"领取任务",RECLAIMED:"恢复执行",SUCCEEDED:"发布结果",FAILED:"执行失败",CANCELLED:"取消任务",RETRY_SCHEDULED:"等待重试",ATTEMPTS_EXHAUSTED:"尝试次数耗尽"};
  for(const e of task.events||[])events.append(node("span",`${eventNames[e.kind]||e.kind} · ${new Date(e.at).toLocaleTimeString("zh-CN")}`));trace.append(events);report.append(details("运行记录",trace));
  report.append(details("完整公开报告与溯源信息",node("pre",JSON.stringify(task,null,2))));
  if(terminal&&task.request.kind==="agent"){const actions=node("div",undefined,"actions");actions.append(action("用这个问题继续分析",()=>{$("question").value=task.request.question;setMode("agent");$("query-panel").open=true;$("workbench").scrollIntoView();$("question").focus();}));report.append(actions);}
}
function revealReport(){if(window.matchMedia("(min-width:1024px)").matches)window.scrollTo({top:0});else $("report").scrollIntoView({block:"start"});}
function selectTask(id){setView("analysis");activeTask=id;const myGeneration=++generation;let first=true;clearTimeout(pollTimer);location.hash="task="+encodeURIComponent(id);const report=$("report");report.replaceChildren(node("p","正在读取任务…","pending"));
  async function poll(){try{const task=await api("/tasks/"+encodeURIComponent(id));if(myGeneration!==generation)return;const reveal=first;if(first){restoreRequest(task);first=false;}renderTask(task);if(reveal)revealReport();if(["QUEUED","RUNNING"].includes(task.status))pollTimer=setTimeout(poll,config.poll_ms);else refreshHistory().catch(e=>notify(errorMessage(e)));}catch(e){if(myGeneration===generation){notify(errorMessage(e));pollTimer=setTimeout(poll,5000);}}}poll();
}
function showSample(index){const sample=samples[index];if(!sample)return;generation++;activeTask=null;clearTimeout(pollTimer);location.hash="sample="+index;setView("analysis");restoreRequest(sample.task);renderTask(sample.task,sample);revealReport();}
function routeFromHash(){if(!config)return;const raw=location.hash.slice(1),hash=new URLSearchParams(raw);if(raw==="history"||raw==="samples"){setView(raw);return;}setView("analysis");if(hash.has("task")){if(activeTask!==hash.get("task"))selectTask(hash.get("task"));}else if(hash.has("sample"))showSample(Number(hash.get("sample")));else if(hash.has("dataset")){applyDataset(hash.get("dataset"));$("query-panel").open=true;}}
window.addEventListener("hashchange",routeFromHash);
async function refreshHistory(append=false){const page=await api("/tasks"+(append&&nextCursor?"?before="+encodeURIComponent(nextCursor):""));if(!append)$("history-list").replaceChildren();for(const task of page.items){const b=node("button",undefined,"history-row");const text=node("div");text.append(node("b",title(task)),node("small",task.dataset?task.dataset.name+" · 修订 "+task.dataset.revision:"内置合成账本"));b.title="任务 "+task.id;const time=node("time",new Date(task.created_at).toLocaleString("zh-CN"));time.dateTime=new Date(task.created_at).toISOString();b.append(text,time,badge(task.status));b.onclick=()=>selectTask(task.id);$("history-list").append(b);}if(!append&&!page.items.length)$("history-list").append(node("p","还没有本机任务。运行一次分析后，记录会出现在这里。","muted"));nextCursor=page.next_cursor;$("more").hidden=!nextCursor;}
$("reconcile-tab").onclick=()=>setMode("reconcile");$("report-confirm").onchange=()=>setMode(mode);
$("metric-tab").onclick=()=>setMode("metric");$("agent-tab").onclick=()=>setMode("agent");$("metric").onchange=updateFields;
$("refresh-history").onclick=()=>refreshHistory().catch(e=>notify(errorMessage(e)));$("more").onclick=async()=>{$("more").disabled=true;try{await refreshHistory(true);}catch(e){notify(errorMessage(e));}finally{$("more").disabled=false;}};
$("analysis-form").onsubmit=async event=>{event.preventDefault();if(submitting||!config||!datasetAvailable)return;notify("");const body={dataset_id:$("dataset-select").value};
  if(mode==="reconcile"){if(!reportClaims.left||!reportClaims.right||!$("report-confirm").checked){notify("请先选择两份报告并确认预览。");return;}body.metric=RECONCILIATION;body.left=reportClaims.left;body.right=reportClaims.right;body.confirmed=true;}
  else if(mode==="agent"){body.kind="agent";body.question=$("question").value.trim();if(!body.question){notify("请先填写分析问题。");return;}}
  else{body.metric=$("metric").value;body.start=$("start").value+"T00:00:00Z";body.end=$("end").value+"T00:00:00Z";if(body.start>=body.end){notify(errors.invalid_period);return;}if(!$("cutoff-field").hidden)body.as_of=$("cutoff").value+"T00:00:00Z";if(!$("product-field").hidden)body.product_id=$("product").value;if(isComparison(body.metric)){body.current_start=$("current-start").value+"T00:00:00Z";body.current_end=$("current-end").value+"T00:00:00Z";body.top_n=Number($("top-n").value);}}
  const fingerprint=JSON.stringify(body);if(!pendingSubmission||pendingSubmission.fingerprint!==fingerprint)pendingSubmission={fingerprint,key:crypto.randomUUID()};
  // Keep the same key across a network failure: the server may already have accepted it.
  submitting=true;setMode(mode);try{const task=await api("/tasks",body,pendingSubmission.key);pendingSubmission=null;selectTask(task.id);refreshHistory().catch(e=>notify(errorMessage(e)));}catch(e){notify(errorMessage(e));}finally{submitting=false;setMode(mode);}
};
async function init(){setMode("metric");try{config=await api("/config");window.setupCommerceSession(config);await loadDatasets();await refreshHistory();
  const response=await fetch("/demo/examples.json");if(!response.ok)throw new Error("historical_examples_unavailable");const archive=await response.json();samples=archive.examples;
  for(const [i,sample] of samples.entries()){const b=node("button",undefined,"sample");b.append(node("small","DeepSeek 历史报告"),node("strong",sample.title),node("span",sample.description));b.onclick=()=>showSample(i);$("sample-list").append(b);}
  routeFromHash();
}catch(e){notify(errorMessage(e));}}
init();
