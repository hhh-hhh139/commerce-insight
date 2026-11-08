package dev.commerceinsight;

import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.function.*;

/** Confirmed untrusted report claims, bound to a selected snapshot and reconciled arithmetically. */
final class ReportReconciliationContract {
    static final String METRIC="report_reconciliation";
    private static final Set<String> SCALARS=Set.of("paid_amount","paid_order_count","refund_amount","event_net","cohort_net","cohort_refunded_order_count","paid_amount_for_product_orders","product_refund_amount");
    private static final Set<String> CLAIM_FIELDS=Set.of("schema_version","name","query","reported_value","currency","amount_scale","result_unit","metric_version","source_sha256");
    private static final DateTimeFormatter TIME=DateTimeFormatter.ofPattern("uuuu-MM-dd'T'HH:mm:ss.SSSSSSxxx").withZone(ZoneOffset.UTC);
    @SuppressWarnings("unchecked") private static Map<String,Object> object(Object value,int status) {
        if(!(value instanceof Map<?,?>))throw new ApiError(status,"invalid_report_object");return (Map<String,Object>)value;
    }
    private static void require(boolean ok) { if(!ok)throw new ApiError(422,"report_reconciliation_result_mismatch"); }
    private static BigInteger number(Object value) {
        require(value instanceof Integer || value instanceof Long || value instanceof Short || value instanceof Byte);
        return new BigInteger(value.toString());
    }
    private static void equalNumber(Object value,BigInteger expected) { require(expected==null?value==null:number(value).equals(expected)); }
    static Map<String,Object> normalize(Map<String,Object> input,String dataset,Function<Map<String,Object>,Map<String,Object>> queryNormalizer) {
        TaskService.fields(input,"dataset_id","metric","left","right","confirmed");
        if(!Boolean.TRUE.equals(input.get("confirmed")))throw new ApiError(400,"report_confirmation_required");
        var result=new TreeMap<String,Object>();result.put("metric",METRIC);result.put("dataset_id",dataset);result.put("confirmed",true);
        for(String side:List.of("left","right")) {
            var raw=object(input.get(side),400);
            if(!raw.keySet().equals(CLAIM_FIELDS) || !"commerce-report-claim-v1".equals(raw.get("schema_version")))throw new ApiError(400,"invalid_report_claim");
            var claim=new TreeMap<String,Object>(raw);
            for(String key:List.of("name","currency","metric_version"))claim.put(key,TaskService.text(raw,key).strip());
            Object value=raw.get("reported_value"),scale=raw.get("amount_scale"),unit=raw.get("result_unit"),sha=raw.get("source_sha256");
            if(!(value instanceof Integer || value instanceof Long) || new BigInteger(value.toString()).abs().compareTo(BigInteger.TEN.pow(17))>0)throw new ApiError(400,"invalid_report_value");
            if(!(scale instanceof Integer n) || n<0 || n>6 || !(unit instanceof String u) || !Set.of("orders","minor_units").contains(u))throw new ApiError(400,"invalid_report_unit");
            if(sha!=null && (!(sha instanceof String h) || !h.matches("[a-f0-9]{64}")))throw new ApiError(400,"invalid_report_source");
            var query=new TreeMap<String,Object>(object(raw.get("query"),400));
            TaskService.fields(query,"metric","start","end","as_of","product_id");
            if(!(query.get("metric") instanceof String metric) || !SCALARS.contains(metric))throw new ApiError(400,"unsupported_report_metric");
            query.put("dataset_id",dataset);query=new TreeMap<>(queryNormalizer.apply(query));query.remove("dataset_id");
            try {
                for(String key:List.of("start","end","as_of"))if(query.containsKey(key)) {
                    Instant time=Instant.parse((String)query.get(key));
                    if(time.getNano()%1000!=0 || time.atOffset(ZoneOffset.UTC).getYear()<1 || time.atOffset(ZoneOffset.UTC).getYear()>9999)throw new ApiError(400,"invalid_report_time_precision");
                    query.put(key,TIME.format(time));
                }
            } catch(DateTimeException e) { throw new ApiError(400,"invalid_report_period"); }
            if(query.containsKey("product_id") && ((String)query.get("product_id")).length()>128)throw new ApiError(400,"invalid_report_product");
            claim.put("query",query);result.put(side,claim);
        }
        return result;
    }
    static void validate(Map<String,Object> request,Map<String,Object> result,Map<String,Object> data,String sourceSha,
                         Function<Object,String> encoder,BiConsumer<Map<String,Object>,Map<String,Object>> metricValidator) {
        require(METRIC.equals(result.get("metric")) && "report-reconciliation-v1".equals(result.get("metric_version")));
        for(String key:List.of("workspace_id","snapshot_id","currency","amount_scale"))require(Objects.equals(data.get(key),result.get(key)));
        var evidence=object(result.get("evidence"),422);require(sourceSha.equals(evidence.get("source_sha256")));
        require(evidence.get("canonical_sha256") instanceof String h && h.matches("[a-f0-9]{64}"));
        var hashes=object(evidence.get("implementation_sha256"),422);require(hashes.keySet().equals(Set.of("reconciliation.py","ledger.py","ledger_db.py")));
        for(Object value:hashes.values())require(value instanceof String h && h.matches("[a-f0-9]{64}"));
        var reports=object(result.get("reports"),422);require(reports.keySet().equals(Set.of("left","right")));
        boolean verified=true;var residuals=new ArrayList<BigInteger>();var calculated=new ArrayList<BigInteger>();
        for(String side:List.of("left","right")) {
            var claim=object(request.get(side),422);var report=object(reports.get(side),422);
            require(claim.equals(report.get("claim")) && TaskService.sha(encoder.apply(claim).getBytes(StandardCharsets.UTF_8)).equals(report.get("claim_sha256")));
            var query=object(claim.get("query"),422);var issues=new ArrayList<String>();Object source=claim.get("source_sha256");
            if(source==null)issues.add("source_unknown");else if(!sourceSha.equals(source))issues.add("source_mismatch");
            if(!"ledger-metrics-v1".equals(claim.get("metric_version")))issues.add("metric_version_mismatch");
            for(String key:List.of("currency","amount_scale"))if(!Objects.equals(claim.get(key),data.get(key)))issues.add(key+"_mismatch");
            String expectedUnit=((String)query.get("metric")).contains("count")?"orders":"minor_units";
            if(!expectedUnit.equals(claim.get("result_unit")))issues.add("result_unit_mismatch");
            BigInteger computed=null,residual=null;
            if(report.get("calculation")==null) {
                issues.add("calculation_unavailable");require(report.get("calculation_error") instanceof String error && !error.isBlank() && error.length()<=160);
            } else {
                var calculation=object(report.get("calculation"),422);metricValidator.accept(query,calculation);
                computed=number(calculation.get("value"));require(report.get("calculation_error")==null);
            }
            require(issues.equals(report.get("issues")) && Objects.equals(issues.isEmpty(),report.get("verified")));
            if(issues.isEmpty())residual=number(claim.get("reported_value")).subtract(computed);else verified=false;
            equalNumber(report.get("recomputed_value"),computed);equalNumber(report.get("residual_value"),residual);
            calculated.add(computed);residuals.add(residual);
        }
        var left=object(request.get("left"),422);var right=object(request.get("right"),422);
        boolean comparable=List.of("currency","amount_scale","result_unit").stream().allMatch(k->Objects.equals(left.get(k),right.get(k)));
        require(Objects.equals(comparable,result.get("comparable_units")));
        var observed=comparable?number(right.get("reported_value")).subtract(number(left.get("reported_value"))):null;
        equalNumber(result.get("observed_delta"),observed);
        require(result.get("differences") instanceof List<?>);var differences=(List<?>)result.get("differences");require(differences.size()<=15);
        var seen=new HashSet<String>();
        for(Object value:differences) {
            var difference=object(value,422);require(difference.get("field") instanceof String field && seen.add(field));
            String field=(String)difference.get("field");
            if(Set.of("currency","amount_scale","result_unit","metric_version","source_sha256").contains(field))
                require(Objects.equals(left.get(field),difference.get("left")) && Objects.equals(right.get(field),difference.get("right")) && !Objects.equals(left.get(field),right.get(field)));
            else if(field.startsWith("query.")) {
                String key=field.substring(6);require(Set.of("metric","start","end","as_of","product_id").contains(key));
                var a=object(left.get("query"),422);var b=object(right.get("query"),422);
                require(Objects.equals(a.get(key),difference.get("left")) && Objects.equals(b.get(key),difference.get("right")) && !Objects.equals(a.get(key),b.get(key)));
            } else require(Set.of("definition","event_time","status_filter","grain","refund_policy").contains(field)
                && difference.get("left") instanceof String a && !a.isBlank() && a.length()<=200
                && difference.get("right") instanceof String b && !b.isBlank() && b.length()<=200 && !a.equals(b));
        }
        for(String key:List.of("currency","amount_scale","result_unit","metric_version","source_sha256"))require(seen.contains(key)==!Objects.equals(left.get(key),right.get(key)));
        var lq=object(left.get("query"),422);var rq=object(right.get("query"),422);
        for(String key:List.of("metric","start","end","as_of","product_id"))require(seen.contains("query."+key)==!Objects.equals(lq.get(key),rq.get(key)));
        String status="needs_review";
        if(verified && comparable) {
            BigInteger delta=calculated.get(1).subtract(calculated.get(0)),unexplained=residuals.get(1).subtract(residuals.get(0));
            equalNumber(result.get("recomputed_delta"),delta);equalNumber(result.get("unexplained_delta"),unexplained);
            var bridge=object(result.get("bridge"),422);require(bridge.get("components") instanceof List<?>);var components=(List<?>)bridge.get("components");require(components.size()==3);
            var expected=List.of(residuals.get(0).negate(),delta,residuals.get(1));BigInteger sum=number(left.get("reported_value"));
            for(int i=0;i<3;i++) {
                var row=object(components.get(i),422);require(List.of("left_correction","query_difference","right_residual").get(i).equals(row.get("code")) && row.get("label") instanceof String label && !label.isBlank() && label.length()<=200);
                equalNumber(row.get("value"),expected.get(i));sum=sum.add(expected.get(i));
            }
            equalNumber(bridge.get("start_value"),number(left.get("reported_value")));equalNumber(bridge.get("end_value"),sum);
            require(sum.equals(number(right.get("reported_value"))));equalNumber(bridge.get("residual_value"),BigInteger.ZERO);
            status=residuals.stream().anyMatch(v->v.signum()!=0)?"discrepancy":differences.isEmpty()?"matched":"explained";
        } else require(result.get("bridge")==null && result.get("recomputed_delta")==null && result.get("unexplained_delta")==null);
        require(status.equals(result.get("status")) && result.get("summary") instanceof String text && !text.isBlank() && text.length()<=10000);
    }
}
