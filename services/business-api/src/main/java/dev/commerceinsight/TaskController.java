package dev.commerceinsight;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
public class TaskController {
    private final TaskService tasks;
    TaskController(TaskService tasks) { this.tasks=tasks; }
    @GetMapping("/health")
    Map<String,Object> health() { return Map.of("status","ok","protocol","task-v1"); }
    @GetMapping("/api/workspaces/{workspace}/tasks")
    Map<String,Object> list(@PathVariable String workspace,@RequestParam(required=false) String before) {
        return tasks.list(workspace,before);
    }
    @PostMapping("/api/workspaces/{workspace}/tasks")
    ResponseEntity<?> create(@PathVariable String workspace,
            @RequestHeader(value="Idempotency-Key",required=false) String key,@RequestBody Map<String,Object> body) {
        return ResponseEntity.accepted().body(tasks.create(workspace,key,body));
    }
    @GetMapping("/api/workspaces/{workspace}/tasks/{id}")
    Map<String,Object> get(@PathVariable String workspace,@PathVariable String id) { return tasks.get(workspace,id); }
    @PostMapping("/api/workspaces/{workspace}/tasks/{id}/cancel")
    Map<String,Object> cancel(@PathVariable String workspace,@PathVariable String id) { return tasks.cancel(workspace,id); }
    @PostMapping("/internal/tasks/claim")
    ResponseEntity<?> claim() {
        var task=tasks.claim(); return task==null ? ResponseEntity.noContent().build() : ResponseEntity.ok(task);
    }
    @PostMapping("/internal/tasks/{id}/heartbeat")
    Map<String,Object> heartbeat(@PathVariable String id,@RequestBody Map<String,Object> body) {
        TaskService.fields(body,"lease_token"); return tasks.heartbeat(id,TaskService.text(body,"lease_token"));
    }
    @PostMapping("/internal/tasks/{id}/complete")
    @SuppressWarnings("unchecked")
    Map<String,Object> complete(@PathVariable String id,@RequestBody Map<String,Object> body) {
        TaskService.fields(body,"lease_token","result");
        if (!(body.get("result") instanceof Map<?,?> result)) throw new ApiError(400,"invalid_result");
        return tasks.complete(id,TaskService.text(body,"lease_token"),(Map<String,Object>)result);
    }
    @PostMapping("/internal/tasks/{id}/fail")
    Map<String,Object> fail(@PathVariable String id,@RequestBody Map<String,Object> body) {
        TaskService.fields(body,"lease_token","retryable","error_code");
        if (!(body.get("retryable") instanceof Boolean retry)) throw new ApiError(400,"invalid_retryable");
        return tasks.fail(id,TaskService.text(body,"lease_token"),retry,TaskService.text(body,"error_code"));
    }
}
