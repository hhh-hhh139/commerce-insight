package dev.commerceinsight;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/** One bounded, read-only analysis job. Business state is not LangGraph state. */
@Service
public class TaskService {
    private static final String WORKSPACE = "demo-shop";
    private static final Set<String> METRICS = Set.of("paid_amount", "paid_order_count", "refund_amount",
        "event_net", "cohort_net", "cohort_refunded_order_count", "paid_amount_for_product_orders", "product_refund_amount");
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final JsonMapper json;
    private final DatasetRepository datasets;
    private final ResourceControls controls;
    private final String datasetBase64, sourceSha;
    private final long leaseMs, retryMs;
    private final int maxAttempts;

    public TaskService(JdbcTemplate db, TransactionTemplate tx, JsonMapper json, DatasetRepository datasets,ResourceControls controls,
            @Value("${commerce.snapshot}") String snapshot,
            @Value("${commerce.lease-ms}") long leaseMs,
            @Value("${commerce.retry-ms}") long retryMs,
            @Value("${commerce.max-attempts}") int maxAttempts) throws Exception {
        this.db = db; this.tx = tx; this.json = json; this.datasets=datasets;this.controls=controls;
        if (leaseMs < 500 || retryMs < 1 || maxAttempts < 1 || maxAttempts > 5) throw new IllegalArgumentException("invalid_task_limits");
        this.leaseMs = leaseMs; this.retryMs = retryMs; this.maxAttempts = maxAttempts;
        if (Files.size(Path.of(snapshot)) > 1_000_000) throw new IllegalArgumentException("demo_snapshot_too_large");
        byte[] bytes = Files.readAllBytes(Path.of(snapshot));
        if (!WORKSPACE.equals(parse(new String(bytes, StandardCharsets.UTF_8)).get("workspace_id")))
            throw new IllegalArgumentException("snapshot_workspace_mismatch");
        datasetBase64 = Base64.getEncoder().encodeToString(bytes); sourceSha = sha(bytes);
    }

    @SuppressWarnings("unchecked")
    private Map<String,Object> parse(String value) { return json.readValue(value, Map.class); }

    private Object ordered(Object value) {
        if (value instanceof Map<?,?> map) {
            var result = new TreeMap<String,Object>();
            map.forEach((k,v) -> result.put(k.toString(), ordered(v))); return result;
        }
        if (value instanceof List<?> list) return list.stream().map(this::ordered).toList();
        return value;
    }
    private String encode(Object value) { return json.writeValueAsString(ordered(value)); }
    static String sha(byte[] bytes) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    static String text(Map<String,Object> input, String key) {
        Object v = input.get(key);
        if (!(v instanceof String s) || s.isBlank() || s.length() > 160) throw new ApiError(400, "invalid_" + key);
        return s;
    }
    static void fields(Map<String,Object> input, String... allowed) {
        if (!Set.of(allowed).containsAll(input.keySet())) throw new ApiError(400,"unknown_field");
    }
    static void workspace(String value) { if (!WORKSPACE.equals(value)) throw new ApiError(403,"workspace_forbidden"); }
    private static String instant(Map<String,Object> input, String key) {
        try { return Instant.parse(text(input,key)).toString(); }
        catch (java.time.DateTimeException e) { throw new ApiError(400,"invalid_" + key); }
    }
    private Map<String,Object> normalize(Map<String,Object> input) {
        String selectedDataset=text(input,"dataset_id");
        if(!selectedDataset.equals("demo-ledger-v1") && !selectedDataset.matches("ds-[a-f0-9-]{36}-r[0-9]{1,2}"))
            throw new ApiError(400,"unsupported_dataset_or_metric");
        if (input.containsKey("kind")) {
            fields(input,"kind","dataset_id","question");
            if (!"agent".equals(input.get("kind")))
                throw new ApiError(400,"unsupported_agent_request");
            if (!(input.get("question") instanceof String q) || q.isBlank() || q.length()>2000)
                throw new ApiError(400,"invalid_question");
            return Map.of("kind","agent","dataset_id",selectedDataset,"question",q.strip());
        }
        if(ReportReconciliationContract.METRIC.equals(input.get("metric")))return ReportReconciliationContract.normalize(input,selectedDataset,this::normalize);
        if(SalesChangeContract.METRIC.equals(input.get("metric")))return SalesChangeContract.normalize(input,selectedDataset);
        if(RefundChangeContract.METRIC.equals(input.get("metric")))return RefundChangeContract.normalize(input,selectedDataset);
        fields(input,"dataset_id","metric","start","end","as_of","product_id");
        var normalized = new TreeMap<String,Object>();
        String dataset = text(input,"dataset_id"), metric = text(input,"metric");
        if (!METRICS.contains(metric)) throw new ApiError(400,"unsupported_dataset_or_metric");
        normalized.put("dataset_id",dataset); normalized.put("metric",metric);
        normalized.put("start",instant(input,"start")); normalized.put("end",instant(input,"end"));
        if (!Instant.parse((String)normalized.get("start")).isBefore(Instant.parse((String)normalized.get("end"))))
            throw new ApiError(400,"invalid_period");
        if (input.containsKey("as_of")) normalized.put("as_of",instant(input,"as_of"));
        if (input.containsKey("product_id")) normalized.put("product_id",text(input,"product_id"));
        return normalized;
    }
    private record Task(String id, String request, String data, String sha, long created, String status,
                        int attempt, String lease, long until, long available, String result, String resultHash, String error,long expires) {}
    private List<Task> rows(String suffix, Object... args) {
        return db.query("SELECT * FROM analysis_task " + suffix, (rs,n) -> new Task(rs.getString("id"),
            rs.getString("request_json"), rs.getString("dataset_base64"), rs.getString("source_sha"), rs.getLong("created_at"),
            rs.getString("status"), rs.getInt("attempt"), rs.getString("lease_token"), rs.getLong("lease_until"),
            rs.getLong("available_at"), rs.getString("result_json"), rs.getString("result_hash"), rs.getString("error_code"),rs.getLong("expires_at")), args);
    }
    private Task task(String id, boolean lock) {
        var found = rows("WHERE id=? AND workspace=?" + (lock ? " FOR UPDATE" : ""), id, WORKSPACE);
        if (found.isEmpty()) throw new ApiError(404,"task_not_found"); return found.getFirst();
    }
    private void event(Task task, String kind) {
        db.update("INSERT INTO task_event(task_id,event_at,kind,attempt) VALUES (?,?,?,?)",task.id,now(),kind,task.attempt);
    }
    private static long now() { return System.currentTimeMillis(); }
    private Map<String,Object> view(Task t) {
        var out = new LinkedHashMap<String,Object>();
        out.put("id",t.id); out.put("workspace_id",WORKSPACE); out.put("status",t.status);
        out.put("attempt",t.attempt); out.put("max_attempts",maxAttempts); out.put("request",parse(t.request));
        out.put("source_sha256",t.sha); out.put("created_at",t.created);
        out.put("expires_at",t.expires);
        out.put("result",t.result == null ? null : parse(t.result)); out.put("error_code",t.error);
        Object dataset=parse(t.request).get("dataset_id");
        if(dataset instanceof String selected && selected.startsWith("ds-"))out.put("dataset",datasets.metadata(WORKSPACE,selected));
        return out;
    }
    public Map<String,Object> create(String ws, String key, Map<String,Object> input) {
        workspace(ws);
        if (key == null || !key.matches("[A-Za-z0-9._:-]{1,128}")) throw new ApiError(400,"invalid_idempotency_key");
        String request = encode(normalize(input));controls.expire();
        try {
            return tx.execute(s -> {
                controls.guard(ws);
                var prior = rows("WHERE workspace=? AND idem=?",WORKSPACE,key);
                if (!prior.isEmpty()) return existing(prior.getFirst(),request);
                controls.admit(ws);
                var normalized=parse(request);
                var selected="demo-ledger-v1".equals(normalized.get("dataset_id")) ? new DatasetRepository.Snapshot(datasetBase64,sourceSha)
                    : datasets.resolve(WORKSPACE,(String)normalized.get("dataset_id"),normalized);
                String id = UUID.randomUUID().toString();
                long created=now();
                db.update("INSERT INTO analysis_task(id,workspace,idem,request_json,dataset_base64,source_sha,created_at,status,expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    id,WORKSPACE,key,request,selected.base64(),selected.sha(),created,"QUEUED",controls.deadline(created));
                Task t=task(id,false); event(t,"CREATED"); return view(t);
            });
        } catch (DuplicateKeyException e) { // Race resolved after the losing transaction rolls back.
            return existing(rows("WHERE workspace=? AND idem=?",WORKSPACE,key).getFirst(),request);
        }
    }
    private Map<String,Object> existing(Task t,String request) {
        if (!t.request.equals(request)) throw new ApiError(409,"idempotency_conflict"); return view(t);
    }
    // Only ImportService can construct this internal job type; public create() rejects it.
    String enqueueImport(String ws,String draftId,int revision,Map<String,Object> payload) {
        workspace(ws);
        controls.admit(ws);
        String id=UUID.randomUUID().toString();byte[] bytes=encode(payload).getBytes(StandardCharsets.UTF_8);
        var request=Map.of("kind","import_check","draft_id",draftId,"revision",revision);
        long created=now();
        db.update("INSERT INTO analysis_task(id,workspace,idem,request_json,dataset_base64,source_sha,created_at,status,category,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            id,WORKSPACE,"import:"+draftId+":"+revision,encode(request),Base64.getEncoder().encodeToString(bytes),sha(bytes),created,"QUEUED","import",controls.deadline(created));
        event(task(id,false),"CREATED");return id;
    }
    public Map<String,Object> get(String ws,String id) {
        workspace(ws);
        controls.expire();
        return tx.execute(s -> {
            Task t = task(id,true); var out=view(t);
            out.put("events", db.query("SELECT kind,attempt,event_at FROM task_event WHERE task_id=? ORDER BY sequence",
                (rs,n) -> Map.of("kind",rs.getString(1),"attempt",rs.getInt(2),"at",rs.getLong(3)),id)); return out;
        });
    }
    public Map<String,Object> list(String ws,String before) {
        workspace(ws);
        controls.expire();
        List<Task> found;
        if (before==null) found=rows("WHERE workspace=? AND category='analysis' ORDER BY created_at DESC,id DESC LIMIT 21",WORKSPACE);
        else {
            Task cursor=task(before,false);
            found=rows("WHERE workspace=? AND category='analysis' AND (created_at<? OR (created_at=? AND id<?)) ORDER BY created_at DESC,id DESC LIMIT 21",
                WORKSPACE,cursor.created,cursor.created,cursor.id);
        }
        var page=found.stream().limit(20).map(t -> {
            var summary=view(t); summary.remove("result"); return summary;
        }).toList();
        var result=new LinkedHashMap<String,Object>(); result.put("items",page);
        result.put("next_cursor",found.size()>20 ? found.get(19).id : null); return result;
    }
    public Map<String,Object> claim() {
        controls.expire();
        return tx.execute(s -> {
            if(!controls.canClaim(WORKSPACE,now()))return null;
            // Candidates can race; ownership is checked again after locking each row.
            var candidates = db.queryForList("SELECT id FROM analysis_task WHERE workspace=? AND ((status='QUEUED' AND available_at<=?) OR (status='RUNNING' AND lease_until<=?)) ORDER BY created_at,id LIMIT 32",String.class,WORKSPACE,now(),now());
            for (String id : candidates) {
                Task t=task(id,true); long time=now();
                boolean ready=t.status.equals("QUEUED") && t.available<=time;
                boolean expired=t.status.equals("RUNNING") && t.until<=time;
                if ((!ready && !expired) || t.expires<=time) continue;
                if (t.attempt>=maxAttempts) {
                    db.update("UPDATE analysis_task SET status='FAILED',error_code='attempts_exhausted' WHERE id=?",id);
                    event(t,"ATTEMPTS_EXHAUSTED"); continue;
                }
                String lease=UUID.randomUUID().toString();
                db.update("UPDATE analysis_task SET status='RUNNING',attempt=attempt+1,lease_token=?,lease_until=?,error_code=NULL WHERE id=?",lease,Math.min(t.expires,time+leaseMs),id);
                Task owned=task(id,false); event(owned,expired ? "RECLAIMED" : "CLAIMED");
                var out=view(owned); out.put("lease_token",lease); out.put("lease_ms",leaseMs);
                out.put("dataset_base64",owned.data); return out;
            }
            return null;
        });
    }
    private void owned(Task t,String lease) {
        if (!t.status.equals("RUNNING") || !Objects.equals(t.lease,lease) || t.until<=now() || t.expires<=now()) throw new ApiError(409,"lease_lost");
    }
    public Map<String,Object> heartbeat(String id,String lease) {
        controls.expire();
        return tx.execute(s -> {
            Task t=task(id,true); owned(t,lease);
            db.update("UPDATE analysis_task SET lease_until=? WHERE id=?",Math.min(t.expires,now()+leaseMs),id);
            return Map.of("lease_ms",leaseMs);
        });
    }
    public Map<String,Object> cancel(String ws,String id) {
        workspace(ws);
        return tx.execute(s -> {
            Task t=task(id,true);
            if (t.status.equals("SUCCEEDED") || t.status.equals("FAILED")) throw new ApiError(409,"already_terminal");
            if (!t.status.equals("CANCELLED")) {
                db.update("UPDATE analysis_task SET status='CANCELLED',lease_until=0 WHERE id=?",id); event(t,"CANCELLED");
            }
            return view(task(id,false));
        });
    }
    public Map<String,Object> complete(String id,String lease,Map<String,Object> result) {
        String encoded=encode(result), hash=sha(encoded.getBytes(StandardCharsets.UTF_8));
        return tx.execute(s -> {
            Task t=task(id,true);
            if (t.status.equals("SUCCEEDED") && Objects.equals(t.lease,lease) && hash.equals(t.resultHash)) return view(t);
            owned(t,lease);
            String published=encoded;
            if("import_check".equals(parse(t.request).get("kind")))published=encode(datasets.acceptCheck(t.id,t.sha,parse(t.request),t.data,result));
            else validateResult(t,result);
            db.update("UPDATE analysis_task SET status='SUCCEEDED',result_json=?,result_hash=?,lease_until=0,error_code=NULL WHERE id=?",published,hash,id);
            event(t,"SUCCEEDED"); return view(task(id,false));
        });
    }
    private void validateResult(Task t,Map<String,Object> result) {
        var request=parse(t.request);
        if(ReportReconciliationContract.METRIC.equals(request.get("metric"))) {
            var data=parse(new String(Base64.getDecoder().decode(t.data),StandardCharsets.UTF_8));
            ReportReconciliationContract.validate(request,result,data,t.sha,this::encode,(query,calculation)->validateMetricResult(t,query,calculation));return;
        }
        if ("agent".equals(request.get("kind"))) { validateAgent(t,request,result); return; }
        validateMetricResult(t,request,result);
    }
    private void validateMetricResult(Task t,Map<String,Object> request,Map<String,Object> result) {
        Object selected=parse(t.request).get("dataset_id");
        if(selected instanceof String id && id.startsWith("ds-")
                && !((List<?>)datasets.metadata(WORKSPACE,id).get("available_metrics")).contains(SalesChangeContract.capability(request.get("metric"))))
            throw new ApiError(422,"dataset_metric_unavailable");
        if(RefundChangeContract.METRIC.equals(request.get("metric"))) {
            var data=parse(new String(Base64.getDecoder().decode(t.data),StandardCharsets.UTF_8));
            RefundChangeContract.validate(request,result,data,t.sha);return;
        }
        if(SalesChangeContract.METRIC.equals(request.get("metric"))) {
            var data=parse(new String(Base64.getDecoder().decode(t.data),StandardCharsets.UTF_8));
            SalesChangeContract.validate(request,result,data,t.sha);return;
        }
        if (!Objects.equals(request.get("metric"),result.get("metric")) || !WORKSPACE.equals(result.get("workspace_id"))
            || !(result.get("value") instanceof Byte || result.get("value") instanceof Short || result.get("value") instanceof Integer || result.get("value") instanceof Long)
            || !(result.get("evidence") instanceof Map<?,?> evidence) || !t.sha.equals(evidence.get("source_sha256"))
            || !(evidence.get("implementation_sha256") instanceof Map<?,?> implementations) || implementations.size()!=2
            || !(result.get("period") instanceof Map<?,?> period)) throw new ApiError(422,"result_evidence_mismatch");
        try {
            for (String k : List.of("start","end"))
                if (!Instant.parse((String)request.get(k)).equals(Instant.parse((String)period.get(k)))) throw new ApiError(422,"result_period_mismatch");
            Object asof=request.get("as_of"), actual=period.get("as_of_exclusive");
            if (asof == null ? actual != null : !Instant.parse((String)asof).equals(Instant.parse((String)actual))) throw new ApiError(422,"result_period_mismatch");
        } catch (ClassCastException | NullPointerException | java.time.DateTimeException e) { throw new ApiError(422,"result_period_mismatch"); }
        // Worker is a trusted service. This checks binding, not mathematical truth.
        var data=parse(new String(Base64.getDecoder().decode(t.data),StandardCharsets.UTF_8));
        String metric=(String)request.get("metric");
        String unit=(metric.equals("paid_order_count") || metric.equals("cohort_refunded_order_count")) ? "orders" : "minor_units";
        if (!Objects.equals(data.get("snapshot_id"),result.get("snapshot_id")) || !Objects.equals(data.get("currency"),result.get("currency"))
            || !Objects.equals(data.get("amount_scale"),result.get("amount_scale")) || !unit.equals(result.get("result_unit"))) throw new ApiError(422,"result_unit_mismatch");
        if (!implementations.keySet().equals(Set.of("ledger.py","ledger_db.py"))
            || implementations.values().stream().anyMatch(v -> !(v instanceof String h) || !h.matches("[a-f0-9]{64}"))
            || !(evidence.get("canonical_sha256") instanceof String canonical) || !canonical.matches("[a-f0-9]{64}")
            || !"ledger-metrics-v1".equals(result.get("metric_version"))
            || !(result.get("sql") instanceof String sql) || sql.isBlank()
            || !(result.get("params") instanceof List<?> params)) throw new ApiError(422,"result_evidence_mismatch");
        if (request.containsKey("product_id") && (params.isEmpty() || !request.get("product_id").equals(params.getLast())))
            throw new ApiError(422,"result_product_mismatch");
    }
    @SuppressWarnings("unchecked")
    private void validateAgent(Task t,Map<String,Object> request,Map<String,Object> report) {
        if (!"agent_report_v1".equals(report.get("kind")) || !WORKSPACE.equals(report.get("workspace_id"))
            || !t.sha.equals(report.get("source_sha256"))
            || !sha(((String)request.get("question")).getBytes(StandardCharsets.UTF_8)).equals(report.get("question_sha256"))
            || !(report.get("answer") instanceof String answer) || answer.isBlank() || answer.length()>8000
            || !(report.get("evidence") instanceof List<?> evidence) || evidence.size()>4
            || !(report.get("comparisons") instanceof List<?> comparisons) || comparisons.size()>2
            || !(report.get("provenance") instanceof Map<?,?> provenance)
            || !t.id.equals(provenance.get("task_id")) || !t.sha.equals(provenance.get("source_sha256"))
            || !sha(t.request.getBytes(StandardCharsets.UTF_8)).equals(provenance.get("request_sha256"))
            || !(provenance.get("attempt") instanceof Integer attempt) || attempt!=t.attempt
            || !(provenance.get("mode") instanceof String mode) || !Set.of("scripted","live").contains(mode)
            || !(report.get("usage") instanceof Map<?,?> usage)
            || !(usage.get("model_attempts") instanceof Integer calls) || calls<1 || calls>5
            || !(usage.get("tool_attempts") instanceof Integer tools) || tools<0 || tools>4
            || !(usage.get("real_model_calls") instanceof Integer real) || real!=(mode.equals("live")?calls:0))
            throw new ApiError(422,"agent_result_binding_mismatch");
        Object outcome=report.get("outcome"), issue=report.get("issue");
        if (!(issue instanceof String)) throw new ApiError(422,"agent_answer_contract");
        if ("answered".equals(outcome)) {
            if (evidence.isEmpty() || !"none".equals(issue)) throw new ApiError(422,"agent_answer_contract");
        } else {
            boolean clarify="needs_clarification".equals(outcome) && Set.of("needs_time_range","needs_metric_definition","needs_refund_cutoff").contains(issue);
            boolean unsupported="unsupported".equals(outcome) && Set.of("missing_data","unsupported_request").contains(issue);
            if ((!clarify && !unsupported) || !evidence.isEmpty() || !comparisons.isEmpty()) throw new ApiError(422,"agent_answer_contract");
        }
        var byId=new HashMap<String,Map<String,Object>>();
        for (Object value : evidence) {
            if (!(value instanceof Map<?,?> item) || !(item.get("query") instanceof Map<?,?> rawQuery)
                || !(item.get("result") instanceof Map<?,?> rawResult) || !(item.get("evidence_id") instanceof String id))
                throw new ApiError(422,"agent_evidence_invalid");
            var query=new HashMap<String,Object>((Map<String,Object>)rawQuery);
            query.put("dataset_id","demo-ledger-v1");
            Map<String,Object> normalized;
            try { normalized=normalize(query); }
            catch (ApiError e) { throw new ApiError(422,"agent_evidence_query_invalid"); }
            validateMetricResult(t,normalized,(Map<String,Object>)rawResult);
            String expected="ev_"+sha(encode(Map.of("query",rawQuery,"result",rawResult)).getBytes(StandardCharsets.UTF_8));
            if (!expected.equals(id) || byId.put(id,(Map<String,Object>)item)!=null) throw new ApiError(422,"agent_evidence_invalid");
        }
        for (Object value : comparisons) {
            if (!(value instanceof Map<?,?> c) || !byId.containsKey(c.get("baseline_id")) || !byId.containsKey(c.get("current_id")))
                throw new ApiError(422,"agent_comparison_invalid");
            var left=byId.get(c.get("baseline_id")); var right=byId.get(c.get("current_id"));
            var a=(Map<String,Object>)left.get("result"); var b=(Map<String,Object>)right.get("result");
            if(Set.of(SalesChangeContract.METRIC,RefundChangeContract.METRIC).contains(a.get("metric")) || Set.of(SalesChangeContract.METRIC,RefundChangeContract.METRIC).contains(b.get("metric")))
                throw new ApiError(422,"agent_comparison_invalid");
            for (String field : List.of("metric","currency","amount_scale","result_unit","workspace_id","snapshot_id"))
                if (!Objects.equals(a.get(field),b.get(field))) throw new ApiError(422,"agent_comparison_invalid");
            var aq=(Map<String,Object>)left.get("query"); var bq=(Map<String,Object>)right.get("query");
            var ap=(Map<String,Object>)a.get("period"); var bp=(Map<String,Object>)b.get("period");
            if (!Objects.equals(aq.get("product_id"),bq.get("product_id")) || !Objects.equals(ap.get("as_of_exclusive"),bp.get("as_of_exclusive")))
                throw new ApiError(422,"agent_comparison_invalid");
            Instant startA=Instant.parse((String)ap.get("start")), endA=Instant.parse((String)ap.get("end"));
            Instant startB=Instant.parse((String)bp.get("start")), endB=Instant.parse((String)bp.get("end"));
            if (endA.isAfter(startB) || !java.time.Duration.between(startA,endA).equals(java.time.Duration.between(startB,endB)))
                throw new ApiError(422,"agent_comparison_invalid");
            var baseline=new java.math.BigInteger(a.get("value").toString());
            var delta=new java.math.BigInteger(b.get("value").toString()).subtract(baseline);
            if (!(c.get("delta") instanceof Number) || !delta.toString().equals(c.get("delta").toString()) || !a.get("result_unit").equals(c.get("result_unit")))
                throw new ApiError(422,"agent_comparison_invalid");
            String percent=baseline.signum()==0 ? null : new java.math.BigDecimal(delta).multiply(java.math.BigDecimal.valueOf(100))
                .divide(new java.math.BigDecimal(baseline),2,java.math.RoundingMode.HALF_UP).toPlainString();
            if (!Objects.equals(percent,c.get("change_percent"))) throw new ApiError(422,"agent_comparison_invalid");
        }
    }
    public Map<String,Object> fail(String id,String lease,boolean retryable,String code) {
        if (!code.matches("[a-z_]{1,80}")) throw new ApiError(400,"invalid_error_code");
        return tx.execute(s -> {
            Task t=task(id,true); owned(t,lease);
            boolean retry=retryable && t.attempt<maxAttempts;
            db.update("UPDATE analysis_task SET status=?,available_at=?,lease_until=0,error_code=? WHERE id=?",retry?"QUEUED":"FAILED",now()+retryMs*t.attempt,code,id);
            event(t,retry?"RETRY_SCHEDULED":"FAILED"); return view(task(id,false));
        });
    }
}
