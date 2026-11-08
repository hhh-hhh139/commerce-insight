package dev.commerceinsight;

import java.math.*;
import java.time.*;
import java.util.*;

/** Binds a trusted Worker's grouped result and checks derived arithmetic, not SQL truth. */
final class SalesChangeContract {
    static final String METRIC="sales_change_by_product";
    static String capability(Object metric) { return METRIC.equals(metric) || ReportReconciliationContract.METRIC.equals(metric)?"paid_amount":RefundChangeContract.METRIC.equals(metric)?"refund_amount":String.valueOf(metric); }
    static Map<String,Object> normalize(Map<String,Object> input,String dataset) {
        TaskService.fields(input,"dataset_id","metric","start","end","current_start","current_end","top_n");
        var result=new TreeMap<String,Object>();result.put("dataset_id",dataset);result.put("metric",METRIC);
        try {
            for(String key:List.of("start","end","current_start","current_end"))result.put(key,Instant.parse(TaskService.text(input,key)).toString());
            Instant a=Instant.parse((String)result.get("start")),b=Instant.parse((String)result.get("end"));
            Instant c=Instant.parse((String)result.get("current_start")),d=Instant.parse((String)result.get("current_end"));
            if(!a.isBefore(b) || !c.isBefore(d) || b.isAfter(c) || !Duration.between(a,b).equals(Duration.between(c,d)))
                throw new ApiError(400,"incomparable_periods");
        } catch(DateTimeException e) { throw new ApiError(400,"invalid_period"); }
        Object top=input.getOrDefault("top_n",5);
        if(!(top instanceof Integer n) || n<1 || n>10)throw new ApiError(400,"invalid_top_n");
        result.put("top_n",top);return result;
    }
    private static void require(boolean condition) { if(!condition)throw new ApiError(422,"sales_change_result_mismatch"); }
    @SuppressWarnings("unchecked")
    private static Map<String,Object> map(Object value) { require(value instanceof Map<?,?>);return (Map<String,Object>)value; }
    private static List<?> list(Object value) { require(value instanceof List<?>);return (List<?>)value; }
    private static BigInteger integer(Object value) {
        require(value instanceof Integer || value instanceof Long || value instanceof Short || value instanceof Byte);
        return new BigInteger(value.toString());
    }
    private static int count(Object value) { var n=integer(value);require(n.signum()>=0 && n.compareTo(BigInteger.valueOf(500000))<=0);return n.intValue(); }
    private static String percent(BigInteger value,BigInteger base) {
        return base.signum()==0?null:new BigDecimal(value).multiply(BigDecimal.valueOf(100)).divide(new BigDecimal(base),2,RoundingMode.HALF_UP).toPlainString();
    }
    static void validate(Map<String,Object> request,Map<String,Object> result,Map<String,Object> data,String sourceSha) {
        require(METRIC.equals(result.get("metric")) && "sales-change-v1".equals(result.get("metric_version"))
            && "minor_units".equals(result.get("result_unit")) && Objects.equals(request.get("top_n"),result.get("top_n")));
        for(String key:List.of("workspace_id","snapshot_id","currency","amount_scale"))require(Objects.equals(data.get(key),result.get(key)));
        var p=map(result.get("period"));
        try { for(String key:List.of("start","end","current_start","current_end"))
            require(Instant.parse((String)request.get(key)).equals(Instant.parse((String)p.get(key))));
        } catch(DateTimeException|ClassCastException|NullPointerException e) { throw new ApiError(422,"sales_change_result_mismatch"); }
        var evidence=map(result.get("evidence"));require(sourceSha.equals(evidence.get("source_sha256")));
        var implementations=map(evidence.get("implementation_sha256"));
        require(implementations.keySet().equals(Set.of("sales_change.py","ledger.py","ledger_db.py")));
        for(Object value:implementations.values())require(value instanceof String h && h.matches("[a-f0-9]{64}"));
        require(evidence.get("canonical_sha256") instanceof String h && h.matches("[a-f0-9]{64}"));
        var queries=list(result.get("queries"));require(queries.size()==3);
        var bounds=List.of(p.get("start"),p.get("end"),p.get("current_start"),p.get("current_end"));
        var expectedParams=List.of(List.of(bounds.get(0),bounds.get(1),bounds.get(2),bounds.get(3),bounds.get(0),bounds.get(1),bounds.get(2),bounds.get(3)),bounds.subList(0,2),bounds.subList(2,4));
        for(int i=0;i<3;i++) {
            var q=map(queries.get(i));require(List.of("product_totals","baseline_total","current_total").get(i).equals(q.get("role"))
                && q.get("sql") instanceof String sql && !sql.isBlank() && expectedParams.get(i).equals(q.get("params")));
        }
        var total=map(result.get("totals"));var a=integer(total.get("baseline_minor"));var b=integer(total.get("current_minor"));var delta=integer(total.get("delta_minor"));
        require(a.signum()>=0 && b.signum()>=0 && b.subtract(a).equals(delta) && Objects.equals(percent(delta,a),total.get("change_percent")));
        var check=map(result.get("reconciliation"));
        for(String key:List.of("baseline_minor","current_minor","delta_minor"))require(integer(check.get(key)).equals(integer(total.get(key))));
        for(String key:List.of("baseline_residual_minor","current_residual_minor","delta_residual_minor"))require(integer(check.get(key)).signum()==0);
        var seen=new HashSet<String>();int products=0;BigInteger directions=BigInteger.ZERO;
        for(String direction:List.of("decreases","increases")) {
            int sign=direction.equals("decreases")?-1:1;var group=map(result.get(direction));var rows=list(group.get("products"));
            int size=count(group.get("product_count"));var groupDelta=integer(group.get("delta_minor"));var other=map(group.get("other"));
            int remaining=count(other.get("product_count"));var otherDelta=integer(other.get("delta_minor"));
            require(rows.size()==Math.min(size,(Integer)request.get("top_n")) && size==rows.size()+remaining);
            require(groupDelta.signum()==(size==0?0:sign) && otherDelta.signum()==(remaining==0?0:sign));
            BigInteger sum=otherDelta;
            for(Object value:rows) {
                var r=map(value);require(r.get("product_id") instanceof String id && !id.isBlank() && id.length()<=200 && seen.add(id));
                var x=integer(r.get("baseline_minor"));var y=integer(r.get("current_minor"));var diff=integer(r.get("delta_minor"));
                require(x.signum()>=0 && y.signum()>=0 && y.subtract(x).equals(diff) && diff.signum()==sign);
                require(Objects.equals(percent(diff,x),r.get("change_percent")) && Objects.equals(percent(diff.abs(),groupDelta.abs()),r.get("direction_share_percent")));
                require((x.signum()==0?"current_only":y.signum()==0?"baseline_only":"both").equals(r.get("period_presence")));
                sum=sum.add(diff);
            }
            require(sum.equals(groupDelta));products+=size;directions=directions.add(groupDelta);
        }
        require(directions.equals(delta) && products+count(result.get("unchanged_count"))==count(result.get("product_count")));
        require(result.get("summary") instanceof String text && !text.isBlank() && text.length()<=10000);
    }
}
