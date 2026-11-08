package dev.commerceinsight;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

/** Persistent immutable versions. Worker validation is trusted; publication is Java-owned. */
@Repository
class DatasetRepository {
    private final JdbcTemplate db;
    private final JsonCodec codec;
    DatasetRepository(JdbcTemplate db,JsonCodec codec) { this.db=db;this.codec=codec; }
    record Snapshot(String base64,String sha) {}
    record Draft(String id,String name,String files,String mapping,long created,int revision,String checkTask,String version,boolean archived) {}
    Draft draft(String ws,String id,boolean lock) {
        TaskService.workspace(ws);
        var rows=db.query("SELECT * FROM dataset_import WHERE id=? AND workspace=?"+(lock?" FOR UPDATE":""),
            (r,n)->new Draft(r.getString("id"),r.getString("name"),r.getString("files_json"),r.getString("mapping_json"),r.getLong("created_at"),r.getInt("revision"),
                r.getString("check_task_id"),r.getString("version_id"),r.getBoolean("archived")),id,ws);
        if(rows.isEmpty())throw new ApiError(404,"import_not_found");return rows.getFirst();
    }
    Snapshot resolve(String ws,String id,Map<String,Object> request) {
        TaskService.workspace(ws);
        var rows=db.query("SELECT v.*,d.archived FROM dataset_version v JOIN dataset_import d ON d.id=v.draft_id WHERE v.workspace=? AND v.id=?",
            (r,n)->Map.of("base64",r.getString("snapshot_base64"),"sha",r.getString("snapshot_sha"),"meta",r.getString("metadata_json"),
                "model",r.getBoolean("model_allowed"),"archived",r.getBoolean("archived")),ws,id);
        if(rows.isEmpty())throw new ApiError(409,"dataset_not_ready");var row=rows.getFirst();
        if(Boolean.TRUE.equals(row.get("archived")))throw new ApiError(409,"dataset_archived");
        if("agent".equals(request.get("kind"))) {
            if(!Boolean.TRUE.equals(row.get("model")))throw new ApiError(403,"model_use_not_confirmed");
        } else {
            var meta=codec.parse((String)row.get("meta"));
            if(!((List<?>)meta.get("available_metrics")).contains(SalesChangeContract.capability(request.get("metric"))))throw new ApiError(422,"dataset_metric_unavailable");
        }
        return new Snapshot((String)row.get("base64"),(String)row.get("sha"));
    }
    Map<String,Object> metadata(String ws,String id) {
        TaskService.workspace(ws);
        var rows=db.query("SELECT v.metadata_json,d.archived FROM dataset_version v JOIN dataset_import d ON d.id=v.draft_id WHERE v.workspace=? AND v.id=?",
            (r,n)->{var m=codec.parse(r.getString(1));m.put("archived",r.getBoolean(2));return m;},ws,id);
        if(rows.isEmpty())throw new ApiError(404,"dataset_not_found");return rows.getFirst();
    }
    List<Map<String,Object>> versions(String ws) {
        TaskService.workspace(ws);
        return db.query("SELECT metadata_json FROM dataset_version v JOIN dataset_import d ON d.id=v.draft_id WHERE v.workspace=? AND d.archived=FALSE ORDER BY v.created_at DESC,v.id DESC LIMIT 50",
            (r,n)->codec.parse(r.getString(1)),ws);
    }
    @SuppressWarnings("unchecked")
    Map<String,Object> acceptCheck(String taskId,String sourceSha,Map<String,Object> request,String inputBase64,Map<String,Object> result) {
        var input=codec.parse(new String(Base64.getDecoder().decode(inputBase64),StandardCharsets.UTF_8));
        if(!"import_check_v1".equals(result.get("kind")) || !taskId.equals(result.get("task_id"))
                || !sourceSha.equals(result.get("input_sha256")) || !"demo-shop".equals(result.get("workspace_id"))
                || !Objects.equals(request.get("draft_id"),result.get("draft_id")) || !Objects.equals(request.get("revision"),result.get("revision"))
                || !Objects.equals(input.get("mapping_sha256"),result.get("mapping_sha256"))
                || !(result.get("valid") instanceof Boolean) || !(result.get("error_count") instanceof Integer count) || count<0
                || !(result.get("errors") instanceof List<?>) || !(result.get("profiles") instanceof Map<?,?>)
                || !(result.get("capabilities") instanceof List<?>))throw new ApiError(422,"import_result_binding_mismatch");
        if(!Objects.equals(input.get("mapping")==null?"profile":"validation",result.get("phase")))throw new ApiError(422,"import_result_binding_mismatch");
        var publicResult=new LinkedHashMap<>(result);publicResult.remove("snapshot_base64");
        String snapshot=null,sha=null;
        if(Boolean.TRUE.equals(result.get("valid"))) {
            if(input.get("mapping")==null || count!=0)throw new ApiError(422,"invalid_import_approval");
            try {
                snapshot=(String)result.get("snapshot_base64");byte[] bytes=Base64.getDecoder().decode(snapshot);sha=TaskService.sha(bytes);
                var data=codec.parse(new String(bytes,StandardCharsets.UTF_8));
                if(bytes.length>1_000_000 || !sha.equals(result.get("snapshot_sha256")) || !"demo-shop".equals(data.get("workspace_id"))
                        || !Objects.equals(data.get("snapshot_id"),"import-"+request.get("draft_id")+"-r"+request.get("revision"))
                        || !"commerce-ledger-v1".equals(data.get("schema_version")))throw new ApiError(422,"import_snapshot_mismatch");
            } catch (IllegalArgumentException|ClassCastException|NullPointerException e) { throw new ApiError(422,"import_snapshot_mismatch"); }
        } else if(result.containsKey("snapshot_base64"))throw new ApiError(422,"invalid_import_approval");
        String review=codec.digest(publicResult);publicResult.put("review_sha256",review);
        db.update("INSERT INTO dataset_check(task_id,draft_id,revision,report_json,mapping_json,snapshot_base64,snapshot_sha,review_sha) VALUES(?,?,?,?,?,?,?,?)",
            taskId,request.get("draft_id"),request.get("revision"),codec.encode(publicResult),codec.encode(input.get("mapping")),snapshot,sha,review);
        return publicResult;
    }
}
