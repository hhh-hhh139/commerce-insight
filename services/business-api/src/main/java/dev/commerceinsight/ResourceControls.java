package dev.commerceinsight;

import java.util.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

/** Single-instance resource policy. Admission callers hold the same DB row lock. */
@Service
class ResourceControls {
    private final JdbcTemplate db;
    private final TransactionTemplate tx;
    private final int total,active,running;
    private final long lifetime;
    private final String storage;
    ResourceControls(JdbcTemplate db,TransactionTemplate tx,
            @Value("${commerce.limits.total:2000}") int total,
            @Value("${commerce.limits.active:32}") int active,
            @Value("${commerce.limits.running:4}") int running,
            @Value("${commerce.limits.lifetime-ms:900000}") long lifetime,
            @Value("${commerce.storage:h2_single_instance}") String storage) {
        if(total<1 || total>10000 || active<1 || active>total || running<1 || running>active
                || lifetime<1000 || lifetime>86400000)throw new IllegalArgumentException("invalid_resource_limits");
        this.db=db;this.tx=tx;this.total=total;this.active=active;this.running=running;this.lifetime=lifetime;this.storage=storage;
    }
    long deadline(long created) { return created+lifetime; }
    void guard(String ws) { db.queryForObject("SELECT workspace FROM dataset_import_guard WHERE workspace=? FOR UPDATE",String.class,ws); }
    private long count(String condition,Object... args) { return db.queryForObject("SELECT COUNT(*) FROM analysis_task WHERE "+condition,Long.class,args); }
    void admit(String ws) {
        guard(ws);
        if(count("workspace=?",ws)>=total)throw new ApiError(429,"task_retention_quota_exceeded");
        if(count("workspace=? AND status IN ('QUEUED','RUNNING')",ws)>=active)throw new ApiError(429,"task_active_quota_exceeded");
    }
    boolean canClaim(String ws,long now) {
        guard(ws);
        db.update("UPDATE dataset_import_guard SET worker_last_poll_at=? WHERE workspace=?",now,ws);
        return count("workspace=? AND status='RUNNING' AND lease_until>? AND expires_at>?",ws,now,now)<running;
    }
    @Scheduled(fixedDelayString="${commerce.expire-scan-ms:1000}")
    public void expire() {
        tx.executeWithoutResult(s->{
            long now=System.currentTimeMillis();
            var ids=db.queryForList("SELECT id FROM analysis_task WHERE status IN ('QUEUED','RUNNING') AND expires_at<=? ORDER BY id LIMIT 64",String.class,now);
            for(String id:ids) {
                var rows=db.queryForList("SELECT status,attempt,expires_at FROM analysis_task WHERE id=? FOR UPDATE",id);
                if(rows.isEmpty())continue;
                var t=rows.getFirst();
                if(!Set.of("QUEUED","RUNNING").contains(t.get("STATUS")) || ((Number)t.get("EXPIRES_AT")).longValue()>now)continue;
                db.update("UPDATE analysis_task SET status='FAILED',lease_until=0,error_code='task_deadline_exceeded' WHERE id=?",id);
                db.update("INSERT INTO task_event(task_id,event_at,kind,attempt) VALUES(?,?,?,?)",id,now,"DEADLINE_EXCEEDED",t.get("ATTEMPT"));
            }
        });
    }
    Map<String,Object> status(String ws) {
        TaskService.workspace(ws);expire();
        var counts=new LinkedHashMap<String,Long>();
        for(String state:List.of("QUEUED","RUNNING","SUCCEEDED","FAILED","CANCELLED"))counts.put(state,0L);
        db.query("SELECT status,COUNT(*) FROM analysis_task WHERE workspace=? GROUP BY status",r->{counts.put(r.getString(1),r.getLong(2));},ws);
        Long oldest=db.queryForObject("SELECT MIN(created_at) FROM analysis_task WHERE workspace=? AND status='QUEUED'",Long.class,ws);
        Long poll=db.queryForObject("SELECT worker_last_poll_at FROM dataset_import_guard WHERE workspace=?",Long.class,ws);
        var out=new LinkedHashMap<String,Object>();
        out.put("workspace_id",ws);out.put("counts",counts);out.put("oldest_queued_age_ms",oldest==null?null:Math.max(0,System.currentTimeMillis()-oldest));
        out.put("worker_last_poll_at",poll==null || poll==0?null:poll);
        out.put("limits",Map.of("total",total,"active",active,"running",running,"lifetime_ms",lifetime));
        out.put("retention","bounded_keep_until_explicit_maintenance");out.put("storage",storage);return out;
    }
}
