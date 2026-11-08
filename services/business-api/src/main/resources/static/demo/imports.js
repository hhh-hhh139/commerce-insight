"use strict";
const $ = id => document.getElementById(id);
const element = (tag, text, className) => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (className) e.className = className;
  return e;
};
const tableNames = { orders: "订单表", items: "商品明细表", refunds: "退款表" };
const fields = {
  orders: { order_id: "订单编号", status: "支付状态", paid_minor: "实付金额", paid_at: "支付时间", currency: "币种" },
  items: { item_id: "明细编号", order_id: "所属订单", product_id: "商品编码", line_minor: "分摊金额" },
  refunds: { refund_id: "退款编号", order_id: "所属订单", item_id: "关联明细", status: "售后状态", amount_minor: "退款金额", refunded_at: "退款成功时间", currency: "币种" }
};
const stateLabels = { paid: "已支付", pending: "待处理 / 未完成", cancelled: "已取消", succeeded: "退款成功", failed: "退款失败" };
const stateSuggestions = { "已支付": "paid", "待支付": "pending", "已取消": "cancelled", "退款成功": "succeeded", "处理中": "pending", "退款失败": "failed" };
const metricNames = { paid_amount: "支付金额", paid_order_count: "支付订单数", paid_amount_for_product_orders: "含指定商品的订单金额", refund_amount: "退款金额", event_net: "期间支付减退款", cohort_net: "订单批次净额", cohort_refunded_order_count: "批次退款订单数", product_refund_amount: "商品退款金额" };
const stages = { CHECKING: "检查中", PROFILED: "待填写映射", BLOCKED: "需要修正", NEEDS_CONFIRMATION: "待确认", READY: "可用于分析", ARCHIVED: "已归档", CHECK_FAILED: "检查任务未完成" };
const reasons = {session_request_rejected: "页面会话已变化，请刷新后重试",
  task_active_quota_exceeded: "排队和执行任务已达上限，请稍后再试", task_retention_quota_exceeded: "历史任务已达到保留上限，请由部署者备份并维护存储", task_deadline_exceeded: "检查超过最长等待与执行时间，请检查 Worker 状态后重试", executor_output_limit: "检查输出超过限额，任务已停止", duplicate_primary_key: "主键重复，请核对源文件；系统不会自动去重", unmapped_status: "存在未映射的状态", invalid_decimal_amount: "金额不是有效的普通十进制数", amount_precision_exceeds_minor_unit: "金额精度超出了分，未自动舍入", amount_must_be_bounded_integer_minor_units: "金额须在允许范围内，且符合正数或零值规则", invalid_identifier: "编号为空、过长或包含首尾空格", timestamp_requires_offset: "时间没有时区偏移，请明确选择源时间含义", timestamp_offset_conflicts_with_mapping: "时间值已带偏移，与所选无时区声明冲突", invalid_timestamp: "时间格式不正确", column_count_mismatch: "这一行的列数与表头不一致", headers_must_be_nonempty_unique_strings: "表头为空、重复或包含首尾空格", csv_parse_or_encoding_error: "CSV 格式或 UTF-8 编码不正确", row_limit_exceeded: "超过每表 2000 行限制", file_size_limit: "超过每文件 128 KiB 限制", column_limit_exceeded: "超过 40 列或列名长度限制", mapped_column_missing: "映射指向了不存在的列", mapped_column_reused: "同一源列被重复映射", invalid_status_mapping: "状态映射不完整或不符合约定", mixed_currency: "币种与声明不一致", orphan_item: "明细找不到对应订单", orphan_refund: "退款找不到对应订单", paid_item_amounts_do_not_reconcile: "已支付订单金额与明细分摊总额不一致", refund_item_order_mismatch: "退款、明细与订单的关联不一致", refund_exceeds_payment: "成功退款总额超过支付额", refund_exceeds_item_amount: "退款金额超过对应明细金额", refund_before_payment: "退款成功时间早于支付时间", payment_after_snapshot: "支付时间晚于快照截至时间", refund_after_snapshot: "退款时间晚于快照截至时间", unpaid_order_has_payment: "未支付订单仍有支付额或支付时间", unsuccessful_refund_has_success_time: "非成功退款仍有成功时间", refund_without_payment: "退款对应的订单尚未支付", empty_table: "上传的表为空，请确认数据完整性", unused_columns: "这些源列未进入分析快照", refunds_not_supplied: "未提供退款表，不能计算退款或净额", product_refund_attribution_incomplete: "部分成功退款缺少商品明细归属，商品退款指标暂不开放", model_use_not_confirmed: "此版本尚未允许模型使用", mapping_revision_conflict: "映射检查版本已变化，请重新打开记录并复核", review_digest_mismatch: "确认内容与检查结果不一致，请重新读取", import_immutable: "已确认或归档的导入不能修改，请新建导入", quality_check_blocked: "还有阻止发布的质量问题", quality_check_not_ready: "请等待检查完成", import_quota_exceeded: "已达到本机 50 次导入上限", invalid_mapping: "请核对字段映射", confirmation_required: "请明确确认数据口径", import_executor_failed: "检查执行未完成，请查看本地任务日志", invalid_import_tables: "需要订单表和商品明细表", public_data_declaration_required: "请确认仅使用公开或合成数据"
};
let current = null, generation = 0, timer = null, dirty = false, pendingUpload = null;
function note(text) { $("import-notice").textContent = text || ""; $("import-notice").hidden = !text; }
function reason(code) { return reasons[String(code).split(":")[0]] || "不符合当前数据契约，请展开检查详情查看定位信息"; }
async function api(path, body, key) {
  const headers = { "X-Commerce-Demo": "1", ...window.commerceSessionHeaders() };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  const response = await fetch("/demo/api" + path, { method: body === undefined ? "GET" : "POST", headers, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) });
  if(response.status===401){location.assign("/login?expired");throw new Error("请重新登录");}
  let data; try { data = await response.json(); } catch (_) { throw new Error(response.status === 413 ? "上传内容超过大小限制" : "服务响应异常，请确认本机服务正在运行"); }
  if (!response.ok) throw new Error(reasons[data.error] || `请求未完成：${data.error}`);
  return data;
}
function option(value, label, selected) { const o = element("option", label); o.value = value; o.selected = !!selected; return o; }
function markDirty() {
  dirty = true; $("mapping-dirty").textContent = "映射已修改，需要重新检查。";
  $("confirm-form").hidden = true; $("contract-confirmed").checked = false;
}
async function listImports() {
  const data = await api("/imports"); $("imports-list").replaceChildren();
  for (const d of data.items) {
    const b = element("button", undefined, "import-item"); b.append(element("b", d.name), element("small", `${new Date(d.created_at).toLocaleString("zh-CN")} · 修订 ${d.revision} · ${d.archived ? "已归档" : d.dataset_id ? "已确认" : "导入草稿"}`));
    b.onclick = () => { openImport(d.id); $("import-detail").scrollIntoView(); }; $("imports-list").append(b);
  }
  if (!data.items.length) $("imports-list").append(element("p", "还没有导入记录。上传文件后，可以在这里继续核对或查看已确认版本。", "muted"));
}
function renderStatuses(table, spec) {
  const box = $("states-" + table); if (!box) return;
  box.replaceChildren(element("h3", "状态值的业务含义"));
  const selected = $("col-" + table + "-status").value;
  if (!selected.startsWith("column:")) { box.append(element("p", "先选择状态来源列。", "field-note")); return; }
  const column = selected.slice(7), profile = current.report.profiles[table];
  const values = profile.distinct_values[column] || [];
  const targets = table === "orders" ? ["paid", "pending", "cancelled"] : ["succeeded", "pending", "failed"];
  for (const [i, raw] of values.entries()) {
    const row = element("div", undefined, "mapping-row"), label = element("label", `源状态：${raw || "（空值，需修正源文件）"}`);
    const select = element("select"); select.id = `state-${table}-${i}`; label.htmlFor = select.id; select.dataset.raw = raw; select.required = true;
    const suggested = spec?.statuses?.[raw] || (targets.includes(raw) ? raw : stateSuggestions[raw]);
    select.append(option("", "请选择业务状态"));
    for (const target of targets) select.append(option(target, stateLabels[target], suggested === target));
    row.append(label, select); box.append(row);
  }
  if (profile.distinct_truncated.includes(column)) box.append(element("p", "该列超过预览的 20 个不同值。未展示的状态不会自动映射，检查可能阻止发布。", "quality-warning quality-line"));
}
function renderMapping(d) {
  const profiles = d.report?.profiles || {};
  const available = profiles.orders?.headers?.length && profiles.items?.headers?.length;
  $("mapping-form").hidden = !available;
  if (!available) return;
  const mapping = d.mapping;
  $("currency").value = mapping?.currency || "CNY";
  $("time-basis").value = mapping?.time_basis || "";
  $("exported-at").value = mapping?.exported_at || "";
  $("table-mappings").replaceChildren();
  for (const table of ["orders", "items", "refunds"].filter(t => profiles[t])) {
    const profile = profiles[table], spec = mapping?.tables?.[table];
    const section = element("section", undefined, "mapping-table"); section.append(element("h3", tableNames[table]));
    const unitRow = element("div", undefined, "mapping-row"), unitLabel = element("label", "这张表的金额单位"), unit = element("select");
    unit.id = "unit-" + table; unitLabel.htmlFor = unit.id; unit.required = true; unit.setAttribute("aria-label", tableNames[table] + "金额单位");
    unit.append(option("", "请选择，系统不会猜测"), option("major", "元 / 币种主单位", spec?.amount_unit === "major"), option("minor", "分 / 币种最小单位", spec?.amount_unit === "minor")); unitRow.append(unitLabel, unit); section.append(unitRow);
    for (const [field, labelText] of Object.entries(fields[table])) {
      const row = element("div", undefined, "mapping-row"), label = element("label", labelText), select = element("select");
      select.id = `col-${table}-${field}`; label.htmlFor = select.id; select.required = true; select.setAttribute("aria-label", tableNames[table] + " / " + labelText);
      select.append(option("", "选择来源列"));
      const nullable = field === "currency" || (table === "refunds" && field === "item_id");
      const suggested = spec ? spec.columns[field] : profile.headers.includes(field) ? field : profile.headers.includes(labelText) ? labelText : undefined;
      if (nullable) select.append(option("constant", field === "currency" ? "使用声明币种（无来源列）" : "无商品明细归属", suggested === null));
      for (const h of profile.headers) select.append(option("column:" + h, `${h} · 示例：${(profile.samples[h] || []).join(" / ").slice(0,60)}`, suggested === h));
      if (field === "status") select.onchange = () => { renderStatuses(table, spec); markDirty(); };
      row.append(label, select); section.append(row);
    }
    if (table !== "items") { const states = element("div", undefined, "status-mappings"); states.id = "states-" + table; section.append(states); }
    $("table-mappings").append(section); renderStatuses(table, spec);
  }
  const immutable = ["READY", "ARCHIVED"].includes(d.state);
  $("mapping-fields").disabled = immutable || d.state === "CHECKING";
  $("check-submit").hidden = immutable; $("check-submit").disabled = d.state === "CHECKING";
  $("mapping-dirty").textContent = "";
}
function readMapping() {
  const mapping = { schema_version: "ledger-csv-map-v1", currency: $("currency").value, time_basis: $("time-basis").value, exported_at: $("exported-at").value, tables: {} };
  for (const table of Object.keys(current.report.profiles)) {
    const spec = { columns: {}, amount_unit: $("unit-" + table).value };
    for (const field of Object.keys(fields[table])) { const v = $(`col-${table}-${field}`).value; spec.columns[field] = v === "constant" ? null : v.slice(7); }
    if (table !== "items") { spec.statuses = Object.create(null); for (const select of $("states-" + table).querySelectorAll("select")) spec.statuses[select.dataset.raw] = select.value; }
    mapping.tables[table] = spec;
  }
  return mapping;
}
function renderQuality(d) {
  const r = d.report; $("quality-section").hidden = !r; if (!r) return;
  $("quality-summary").replaceChildren(); $("quality-errors").replaceChildren(); $("quality-warnings").replaceChildren(); $("capabilities").replaceChildren();
  const message = r.error_count ? `发现 ${r.error_count} 项阻止发布的问题。整批数据未发布，没有自动丢弃坏行。` : r.phase === "profile" ? "已读取文件结构。接下来需要填写映射并检查业务口径。" : d.dataset_id ? "检查通过，数据版本已确认。下方能力限制仍适用于此版本。" : "检查通过。请核对下方能力限制，再确认数据版本。";
  $("quality-summary").append(element("p", message, "quality-line"));
  for (const [table, counts] of Object.entries(r.normalized_rows || {})) $("quality-summary").append(element("p", `${tableNames[table]}：输入 ${counts.input} 行 = 转换有效 ${counts.valid_rows} 行 + 无效 ${counts.invalid_rows} 行。关联与金额核对另行检查。`, "field-note"));
  for (const e of r.errors) $("quality-errors").append(element("p", `${e.table ? tableNames[e.table] + " · " : ""}${e.row ? "CSV 记录序号 " + e.row + " · " : ""}${e.field ? (fields[e.table]?.[e.field] || e.field) + " · " : ""}${reason(e.code)}`, "quality-line quality-error"));
  for (const w of r.warnings) $("quality-warnings").append(element("p", `${w.table ? tableNames[w.table] + "：" : ""}${reason(w.code)}${w.columns ? "：" + w.columns.join("、") : ""}`, "quality-line quality-warning"));
  const caps = element("div", undefined, "capability-list");
  for (const cap of r.capabilities) caps.append(element("span", `${cap.available ? "可用" : "不可用"} · ${metricNames[cap.metric]}`, cap.available ? "" : "unavailable"));
  $("capabilities").append(caps); $("quality-json").textContent = JSON.stringify(r, null, 2);
  $("confirm-form").hidden = d.state !== "NEEDS_CONFIRMATION" || dirty;
  $("contract-confirmed").checked = false; $("allow-model").checked = false;
}
function render(d) {
  current = d; dirty = false; $("import-detail").hidden = false;
  const step=["READY","NEEDS_CONFIRMATION","ARCHIVED","BLOCKED"].includes(d.state)?2:d.report?1:0;
  for(const [i,id] of ["step-upload","step-map","step-confirm"].entries()){
    $(id).classList.toggle("current",i===step);$(id).classList.toggle("complete",i<step);
    if(i===step)$(id).setAttribute("aria-current","step");else $(id).removeAttribute("aria-current");
  }
  $("draft-state").className="status "+(["CHECK_FAILED","BLOCKED"].includes(d.state)?"failed":d.state==="CHECKING"?"running":d.state==="ARCHIVED"?"cancelled":"");
  $("draft-title").textContent = d.name; $("draft-state").textContent = stages[d.state] || d.state;
  $("draft-meta").textContent = `导入 ${d.id} · 映射修订 ${d.revision}${d.state === "CHECKING" ? " · 后台检查中，可离开页面，稍后从记录中恢复" : ""}`;
  $("structure-summary").replaceChildren();
  if (d.report) {
    const table = element("table", undefined, "source-table"), head = element("tr");
    for (const title of ["表", "文件", "已读取行数", "来源 SHA-256（前 12 位）"]) head.append(element("th", title)); table.append(head);
    for (const [name, file] of Object.entries(d.report.files)) { const row = element("tr"); for (const text of [tableNames[name], file.name, String(d.report.profiles[name]?.row_count || 0), file.sha256.slice(0,12)]) row.append(element("td", text)); table.append(row); }
    $("structure-summary").append(table);
  }
  if (d.state === "CHECK_FAILED") $("structure-summary").append(element("p", reason(d.error_code), "quality-line quality-error"));
  renderMapping(d); renderQuality(d);
  $("ready-actions").hidden = d.state !== "READY";
  if (d.dataset_id) $("analyze-dataset").href = "/demo/index.html#dataset=" + encodeURIComponent(d.dataset_id);
  $("archive-import").hidden = d.state === "ARCHIVED";
}
function openImport(id) {
  const mine = ++generation; clearTimeout(timer); location.hash = "import=" + encodeURIComponent(id);
  async function poll() {
    try { const d = await api("/imports/" + encodeURIComponent(id)); if (mine !== generation) return; render(d); if (d.state === "CHECKING") timer = setTimeout(poll,1200); }
    catch (e) { if (mine === generation) note(e.message); }
  }
  poll();
}
async function filePayload(file) {
  if (!file.name.toLowerCase().endsWith(".csv") || file.size > 131072 || !file.size) throw new Error("请选择非空 CSV，每文件不超过 128 KiB。");
  const bytes = new Uint8Array(await file.arrayBuffer()); let raw = "";
  for (let i=0; i<bytes.length; i+=32768) raw += String.fromCharCode(...bytes.subarray(i,i+32768));
  return { name: file.name, content_base64: btoa(raw) };
}
$("upload-form").onsubmit = async event => {
  event.preventDefault(); note(""); $("upload-submit").disabled = true;
  try {
    const files = {}; for (const table of ["orders","items","refunds"]) { const file = $(table+"-file").files[0]; if (file) files[table] = await filePayload(file); }
    const body = { name: $("import-name").value.trim(), files, source_declaration: "public_or_synthetic" };
    const fingerprint = JSON.stringify(body); if (pendingUpload?.fingerprint !== fingerprint) pendingUpload = { fingerprint, key: crypto.randomUUID() };
    const d = await api("/imports", body, pendingUpload.key); pendingUpload = null; render(d); openImport(d.id); await listImports(); $("import-detail").scrollIntoView();
  } catch (e) { note(e.message); } finally { $("upload-submit").disabled = false; }
};
$("mapping-fields").addEventListener("input", markDirty);
$("mapping-fields").addEventListener("change", markDirty);
$("mapping-form").onsubmit = async event => {
  event.preventDefault(); if (!current) return; note(""); $("check-submit").disabled = true;
  try { const d = await api("/imports/"+current.id+"/check", { expected_revision: current.revision, mapping: readMapping() }); render(d); openImport(d.id); await listImports(); }
  catch (e) { note(e.message); $("check-submit").disabled = false; }
};
$("confirm-form").onsubmit = async event => {
  event.preventDefault(); if (!current || dirty) return; note(""); $("confirm-submit").disabled = true;
  try { const d = await api("/imports/"+current.id+"/confirm", { revision: current.revision, check_task_id: current.check_task_id, review_sha256: current.report.review_sha256, acknowledge_contract: $("contract-confirmed").checked, allow_model_use: $("allow-model").checked }); render(d); await listImports(); }
  catch (e) { note(e.message); } finally { $("confirm-submit").disabled = false; }
};
$("archive-import").onclick = async () => {
  if (!current) return; $("archive-import").disabled = true;
  try { const d = await api("/imports/"+current.id+"/archive", { expected_revision: current.revision }); generation++; clearTimeout(timer); render(d); await listImports(); }
  catch (e) { note(e.message); } finally { $("archive-import").disabled = false; }
};
$("imports-refresh").onclick = () => listImports().catch(e=>note(e.message));
api("/config").then(c=>{window.setupCommerceSession(c);return listImports();}).then(()=>{ const id = new URLSearchParams(location.hash.slice(1)).get("import"); if (id) openImport(id); }).catch(e=>note(e.message));
