package dev.commerceinsight;

import java.util.*;
import org.springframework.stereotype.Service;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.transaction.support.TransactionTemplate;

/** Source uploads are immutable; mapping checks are revisioned; publication requires review. */
@Service
class ImportService {
    private final DatasetRepository repo;
    private final TaskService tasks;
    private final TransactionTemplate tx;
    private final JsonCodec codec;
    private final JdbcTemplate db;
    ImportService(DatasetRepository repo,TaskService tasks,TransactionTemplate tx,JsonCodec codec,JdbcTemplate db) {
        this.repo=repo;this.tasks=tasks;this.tx=tx;this.codec=codec;this.db=db;
    }
    @SuppressWarnings("unchecked")
    Map<String,Object> upload(String ws,String key,Map<String,Object> body) {
        TaskService.workspace(ws);TaskService.fields(body,"name","files","source_declaration");
        if(key==null || !key.matches("[A-Za-z0-9._:-]{1,128}"))throw new ApiError(400,"invalid_idempotency_key");
        String name=TaskService.text(body,"name");if(name.length()>120)throw new ApiError(400,"invalid_import_name");
        if(!"public_or_synthetic".equals(body.get("source_declaration")))throw new ApiError(400,"public_data_declaration_required");
        if(!(body.get("files") instanceof Map<?,?> files) || !files.keySet().containsAll(Set.of("orders","items"))
                || !Set.of("orders","items","refunds").containsAll(files.keySet()))throw new ApiError(400,"invalid_import_tables");
        var clean=new TreeMap<String,Object>();
        for(var entry:files.entrySet()) {
            if(!(entry.getValue() instanceof Map<?,?> raw))throw new ApiError(400,"invalid_file");
            var file=(Map<String,Object>)raw;TaskService.fields(file,"name","content_base64");
            String filename=TaskService.text(file,"name");
            if(filename.length()>120 || !filename.toLowerCase(Locale.ROOT).endsWith(".csv") || filename.contains("/") || filename.contains("\\")
                    || filename.chars().anyMatch(c->c<32))throw new ApiError(400,"invalid_csv_filename");
            if(!(file.get("content_base64") instanceof String encoded) || encoded.length()>174764)throw new ApiError(413,"file_size_limit");
            try {var decoded=Base64.getDecoder().decode(encoded);if(decoded.length>131072 || decoded.length==0)throw new ApiError(413,"file_size_limit");}
            catch(IllegalArgumentException e){throw new ApiError(400,"invalid_file_encoding");}
            clean.put((String)entry.getKey(),Map.of("name",filename,"content_base64",encoded));
        }
        String uploadHash=codec.digest(body);
        return tx.execute(s->{
            db.queryForObject("SELECT workspace FROM dataset_import_guard WHERE workspace=? FOR UPDATE",String.class,ws);
            var prior=db.queryForList("SELECT id,upload_hash FROM dataset_import WHERE workspace=? AND idem=?",ws,key);
            if(!prior.isEmpty()) {
                if(!uploadHash.equals(prior.getFirst().get("UPLOAD_HASH")))throw new ApiError(409,"idempotency_conflict");
                return get(ws,(String)prior.getFirst().get("ID"));
            }
            if(db.queryForObject("SELECT COUNT(*) FROM dataset_import WHERE workspace=?",Integer.class,ws)>=50)throw new ApiError(429,"import_quota_exceeded");
            String id=UUID.randomUUID().toString();
            db.update("INSERT INTO dataset_import(id,workspace,idem,upload_hash,name,files_json,created_at) VALUES(?,?,?,?,?,?,?)",
                id,ws,key,uploadHash,name,codec.encode(clean),System.currentTimeMillis());
            String task=tasks.enqueueImport(ws,id,0,payload(ws,id,0,clean,null));
            db.update("UPDATE dataset_import SET check_task_id=? WHERE id=?",task,id);
            return get(ws,id);
        });
    }
    private Map<String,Object> payload(String ws,String id,int revision,Object files,Object mapping) {
        var p=new LinkedHashMap<String,Object>();p.put("workspace_id",ws);p.put("draft_id",id);p.put("revision",revision);
        p.put("files",files);p.put("mapping",mapping);p.put("mapping_sha256",codec.digest(mapping));return p;
    }
    private static int revision(Map<String,Object> body,String name) {
        if(!(body.get(name) instanceof Integer value) || value<0)throw new ApiError(400,"invalid_revision");return value;
    }
    Map<String,Object> check(String ws,String id,Map<String,Object> body) {
        TaskService.fields(body,"expected_revision","mapping");int expected=revision(body,"expected_revision");
        if(!(body.get("mapping") instanceof Map<?,?> mapping) || codec.encode(mapping).length()>16000)throw new ApiError(400,"invalid_mapping");
        return tx.execute(s->{
            var draft=repo.draft(ws,id,true);
            if(draft.archived() || draft.version()!=null)throw new ApiError(409,"import_immutable");
            if(expected!=draft.revision())throw new ApiError(409,"mapping_revision_conflict");
            if(expected>=20)throw new ApiError(429,"mapping_revision_limit");
            int next=expected+1;
            String task=tasks.enqueueImport(ws,id,next,payload(ws,id,next,codec.parse(draft.files()),mapping));
            db.update("UPDATE dataset_import SET revision=?,check_task_id=?,mapping_json=? WHERE id=?",next,task,codec.encode(mapping),id);
            return get(ws,id);
        });
    }
    Map<String,Object> get(String ws,String id) {
        var d=repo.draft(ws,id,false);
        var task=tasks.get(ws,d.checkTask());
        @SuppressWarnings("unchecked") var report=(Map<String,Object>)task.get("result");
        String state=d.archived()?"ARCHIVED":d.version()!=null?"READY":Set.of("QUEUED","RUNNING").contains(task.get("status"))?"CHECKING":
            !"SUCCEEDED".equals(task.get("status"))?"CHECK_FAILED":Boolean.TRUE.equals(report.get("valid"))?"NEEDS_CONFIRMATION":
            "profile".equals(report.get("phase")) && Integer.valueOf(0).equals(report.get("error_count"))?"PROFILED":"BLOCKED";
        var out=new LinkedHashMap<String,Object>();out.put("id",d.id());out.put("name",d.name());out.put("created_at",d.created());out.put("state",state);
        out.put("revision",d.revision());out.put("check_task_id",d.checkTask());out.put("dataset_id",d.version());out.put("mapping",codec.parse(d.mapping()));
        out.put("report",report);out.put("task_status",task.get("status"));out.put("error_code",task.get("error_code"));
        if(d.version()!=null)out.put("dataset",repo.metadata(ws,d.version()));return out;
    }
    List<Map<String,Object>> list(String ws) {
        TaskService.workspace(ws);
        return db.query("SELECT id,name,created_at,revision,version_id,archived FROM dataset_import WHERE workspace=? ORDER BY created_at DESC,id DESC LIMIT 50",
            (r,n)->{var m=new LinkedHashMap<String,Object>();m.put("id",r.getString(1));m.put("name",r.getString(2));m.put("created_at",r.getLong(3));
                m.put("revision",r.getInt(4));m.put("dataset_id",r.getString(5));m.put("archived",r.getBoolean(6));return m;},ws);
    }
    @SuppressWarnings("unchecked")
    Map<String,Object> confirm(String ws,String id,Map<String,Object> body) {
        TaskService.fields(body,"revision","check_task_id","review_sha256","acknowledge_contract","allow_model_use");
        int revision=revision(body,"revision");String task=TaskService.text(body,"check_task_id"),review=TaskService.text(body,"review_sha256");
        if(!Boolean.TRUE.equals(body.get("acknowledge_contract")) || !(body.get("allow_model_use") instanceof Boolean allowed))throw new ApiError(400,"confirmation_required");
        return tx.execute(s->{
            var draft=repo.draft(ws,id,true);
            if(draft.archived())throw new ApiError(409,"import_immutable");
            if(draft.revision()!=revision || !task.equals(draft.checkTask()))throw new ApiError(409,"mapping_revision_conflict");
            record Checked(String report,String mapping,String snapshot,String sha,String review) {}
            var checks=db.query("SELECT c.* FROM dataset_check c JOIN analysis_task t ON t.id=c.task_id WHERE c.task_id=? AND c.draft_id=? AND c.revision=? AND t.status='SUCCEEDED'",
                (r,n)->new Checked(r.getString("report_json"),r.getString("mapping_json"),r.getString("snapshot_base64"),r.getString("snapshot_sha"),r.getString("review_sha")),task,id,revision);
            if(checks.isEmpty())throw new ApiError(409,"quality_check_not_ready");var c=checks.getFirst();var quality=codec.parse(c.report());
            if(!review.equals(c.review()))throw new ApiError(409,"review_digest_mismatch");
            if(!Boolean.TRUE.equals(quality.get("valid")) || c.snapshot()==null)throw new ApiError(422,"quality_check_blocked");
            if(draft.version()!=null) {
                var existing=repo.metadata(ws,draft.version());
                if(!Objects.equals(existing.get("model_allowed"),allowed))throw new ApiError(409,"import_immutable");return get(ws,id);
            }
            String dataset="ds-"+id+"-r"+revision;
            var meta=new LinkedHashMap<String,Object>();meta.put("id",dataset);meta.put("name",draft.name());meta.put("import_id",id);
            meta.put("revision",revision);meta.put("check_task_id",task);meta.put("review_sha256",review);meta.put("source_sha256",c.sha());
            for(String field:List.of("snapshot_id","currency","exported_at","mapping_sha256","files","capabilities","warnings"))meta.put(field,quality.get(field));
            meta.put("mapping",codec.parse(c.mapping()));meta.put("amount_scale",2);meta.put("model_allowed",allowed);
            meta.put("available_metrics",((List<Map<String,Object>>)quality.get("capabilities")).stream().filter(m->Boolean.TRUE.equals(m.get("available"))).map(m->m.get("metric")).toList());
            long created=System.currentTimeMillis();meta.put("created_at",created);
            db.update("INSERT INTO dataset_version(id,workspace,draft_id,check_task_id,metadata_json,snapshot_base64,snapshot_sha,model_allowed,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                dataset,ws,id,task,codec.encode(meta),c.snapshot(),c.sha(),allowed,created);
            db.update("UPDATE dataset_import SET version_id=? WHERE id=?",dataset,id);return get(ws,id);
        });
    }
    Map<String,Object> archive(String ws,String id,Map<String,Object> body) {
        TaskService.fields(body,"expected_revision");int expected=revision(body,"expected_revision");
        return tx.execute(s->{
            var draft=repo.draft(ws,id,true);
            if(expected!=draft.revision())throw new ApiError(409,"mapping_revision_conflict");
            db.update("UPDATE dataset_import SET archived=TRUE WHERE id=?",id);
            var job=tasks.get(ws,draft.checkTask());
            if(Set.of("QUEUED","RUNNING").contains(job.get("status")))tasks.cancel(ws,draft.checkTask());
            return get(ws,id);
        });
    }
}
