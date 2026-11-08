package dev.commerceinsight;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.security.web.csrf.CsrfToken;

/** A local showcase, not a production identity system. Never accepts model keys. */
@RestController
class DemoController {
    private final TaskService tasks;
    private final boolean agentEnabled;
    private final OwnerAccess owner;
    static final String SAMPLE_SHA="27048415cf67ccf74b818b62d046eb1d80eff6006e3966c8d7491bac25429cd4";
    DemoController(TaskService tasks,OwnerAccess owner,@Value("${commerce.demo.enabled:false}") boolean enabled,
            @Value("${commerce.demo.agent-enabled:false}") boolean agentEnabled,
            @Value("${commerce.snapshot}") String snapshot) throws Exception {
        this.tasks=tasks; this.agentEnabled=agentEnabled;this.owner=owner;
        if (enabled && !SAMPLE_SHA.equals(TaskService.sha(Files.readAllBytes(Path.of(snapshot)))))
            throw new IllegalArgumentException("demo_requires_reviewed_synthetic_snapshot");
    }
    @GetMapping("/demo/api/config")
    Map<String,Object> config(HttpServletRequest request) {
        var out=new java.util.LinkedHashMap<String,Object>(Map.of("agent_enabled",agentEnabled,"dataset_id","demo-ledger-v1","workspace_id","demo-shop",
            "source_sha256",SAMPLE_SHA,"data_kind","synthetic","poll_ms",1200,"owner_mode",owner.enabled));
        if(owner.enabled) {
            var token=(CsrfToken)request.getAttribute(CsrfToken.class.getName());
            out.put("csrf_header",token.getHeaderName());out.put("csrf_token",token.getToken());
        }
        return out;
    }
    @GetMapping("/demo/api/tasks")
    Map<String,Object> list(@RequestParam(required=false) String before) { return tasks.list("demo-shop",before); }
    @PostMapping("/demo/api/tasks")
    ResponseEntity<?> create(@RequestHeader(value="Idempotency-Key",required=false) String key,@RequestBody Map<String,Object> body) {
        if (body.containsKey("kind") && !agentEnabled) throw new ApiError(409,"live_agent_disabled");
        return ResponseEntity.accepted().body(tasks.create("demo-shop",key,body));
    }
    @GetMapping("/demo/api/tasks/{id}")
    Map<String,Object> get(@PathVariable String id) { return tasks.get("demo-shop",id); }
    @PostMapping("/demo/api/tasks/{id}/cancel")
    Map<String,Object> cancel(@PathVariable String id) { return tasks.cancel("demo-shop",id); }
}
