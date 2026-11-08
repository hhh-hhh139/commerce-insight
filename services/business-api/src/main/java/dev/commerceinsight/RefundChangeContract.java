package dev.commerceinsight;

import java.math.*;
import java.time.*;
import java.util.*;

/** Trusted worker binding and additive reconciliation; independent SQL audit lives in Python. */
final class RefundChangeContract {
    static final String METRIC="refund_change";
    static Map<String,Object> normalize(Map<String,Object> input,String dataset) {
        // Same equal-duration comparison request, a separate result contract.
        var result=SalesChangeContract.normalize(input,dataset);result.put("metric",METRIC);return result;
    }
    private static void require(boolean ok) { if(!ok)throw new ApiError(422,"refund_change_result_mismatch"); }
    @SuppressWarnings("unchecked")
    private static Map<String,Object> map(Object value) { require(value instanceof Map<?,?>);return (Map<String,Object>)value; }
    private static List<?> list(Object value) { require(value instanceof List<?>);return (List<?>)value; }
    private static BigInteger integer(Object value) {
        require(value instanceof Integer || value instanceof Long || value instanceof Short || value instanceof Byte);
        return new BigInteger(value.toString());
    }
    private static int count(Object value) {
        var n=integer(value);require(n.signum()>=0 && n.compareTo(BigInteger.valueOf(500000))<=0);return n.intValue();
    }
    private static String percent(BigInteger value,BigInteger base) {
        return base.signum()==0?null:new BigDecimal(value).multiply(BigDecimal.valueOf(100)).divide(new BigDecimal(base),2,RoundingMode.HALF_UP).toPlainString();
    }
    private static void amounts(Map<String,Object> row) {
        var a=integer(row.get("baseline_minor"));var b=integer(row.get("current_minor"));
        require(a.signum()>=0 && b.signum()>=0 && b.subtract(a).equals(integer(row.get("delta_minor"))));
    }
    static void validate(Map<String,Object> request,Map<String,Object> result,Map<String,Object> data,String sourceSha) {
        require(METRIC.equals(result.get("metric")) && "refund-change-v1".equals(result.get("metric_version"))
            && "minor_units".equals(result.get("result_unit")) && Objects.equals(request.get("top_n"),result.get("top_n")));
        for(String key:List.of("workspace_id","snapshot_id","currency","amount_scale"))require(Objects.equals(data.get(key),result.get(key)));
        var p=map(result.get("period"));
        try { for(String key:List.of("start","end","current_start","current_end"))
            require(Instant.parse((String)request.get(key)).equals(Instant.parse((String)p.get(key))));
        } catch(DateTimeException|ClassCastException|NullPointerException e) { throw new ApiError(422,"refund_change_result_mismatch"); }
        var evidence=map(result.get("evidence"));require(sourceSha.equals(evidence.get("source_sha256")));
        var implementations=map(evidence.get("implementation_sha256"));
        require(implementations.keySet().equals(Set.of("refund_change.py","ledger.py","ledger_db.py")));
        for(Object value:implementations.values())require(value instanceof String h && h.matches("[a-f0-9]{64}"));
        require(evidence.get("canonical_sha256") instanceof String h && h.matches("[a-f0-9]{64}"));
        var queries=list(result.get("queries"));require(queries.size()==4);
        var bounds=List.of(p.get("start"),p.get("end"),p.get("current_start"),p.get("current_end"));
        var expectedParams=List.of(bounds,bounds,bounds.subList(0,2),bounds.subList(2,4));
        for(int i=0;i<4;i++) {
            var q=map(queries.get(i));require(List.of("refund_groups","refund_counts","baseline_total","current_total").get(i).equals(q.get("role"))
                && q.get("sql") instanceof String sql && !sql.isBlank() && expectedParams.get(i).equals(q.get("params")));
        }
        var total=map(result.get("totals"));amounts(total);
        require(Objects.equals(percent(integer(total.get("delta_minor")),integer(total.get("baseline_minor"))),total.get("change_percent")));
        var check=map(result.get("reconciliation"));
        for(String key:List.of("baseline_minor","current_minor","delta_minor"))require(integer(check.get(key)).equals(integer(total.get(key))));
        for(String key:List.of("baseline_residual_minor","current_residual_minor","delta_residual_minor"))require(integer(check.get(key)).signum()==0);
        var origins=list(result.get("origins"));require(origins.size()==2);
        for(int i=0;i<2;i++) {
            var row=map(origins.get(i));require(List.of("prior_orders","same_period_orders").get(i).equals(row.get("origin")));amounts(row);
        }
        var attribution=map(result.get("attribution"));var counts=map(result.get("counts"));
        for(String kind:List.of("linked","unattributed"))require(integer(attribution.get("current_"+kind+"_minor")).subtract(integer(attribution.get("baseline_"+kind+"_minor"))).equals(integer(attribution.get("delta_"+kind+"_minor"))));
        var products=map(result.get("products"));var rows=list(products.get("rows"));var other=map(products.get("other"));
        int size=count(products.get("product_count")),rest=count(other.get("product_count"));amounts(other);
        require(rows.size()==Math.min(size,(Integer)request.get("top_n")) && size==rows.size()+rest);
        var seen=new HashSet<String>();BigInteger previousAbs=null;String previousId=null;
        for(Object value:rows) {
            var row=map(value);amounts(row);
            require(row.get("product_id") instanceof String id && !id.isBlank() && id.length()<=200 && seen.add(id));
            var diff=integer(row.get("delta_minor"));String id=(String)row.get("product_id");
            require(Objects.equals(percent(diff,integer(row.get("baseline_minor"))),row.get("change_percent")));
            // Python orders Unicode code points, whereas String.compareTo orders UTF-16 units.
            require(previousAbs==null || previousAbs.compareTo(diff.abs())>0 || (previousAbs.equals(diff.abs()) && Arrays.compare(previousId.codePoints().toArray(),id.codePoints().toArray())<0));
            previousAbs=diff.abs();previousId=id;
        }
        for(String label:List.of("baseline","current")) {
            String key=label+"_minor";var amount=integer(total.get(key));
            require(integer(map(origins.get(0)).get(key)).add(integer(map(origins.get(1)).get(key))).equals(amount));
            var linked=integer(attribution.get(label+"_linked_minor"));var unknown=integer(attribution.get(label+"_unattributed_minor"));
            require(linked.signum()>=0 && unknown.signum()>=0 && linked.add(unknown).equals(amount)
                && Objects.equals(percent(linked,amount),attribution.get(label+"_coverage_percent")));
            BigInteger sum=integer(other.get(key));require(rest!=0 || sum.signum()==0);
            for(Object value:rows)sum=sum.add(integer(map(value).get(key)));
            require(sum.equals(linked));
            int refunds=count(counts.get(label+"_refunds")),orders=count(counts.get(label+"_orders"));
            require(orders<=refunds && ((orders==0)==(refunds==0)) && (refunds!=0 || amount.signum()==0));
        }
        require(result.get("summary") instanceof String text && !text.isBlank() && text.length()<=10000);
    }
}
