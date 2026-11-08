package dev.commerceinsight;

import java.util.Map;
import java.util.List;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
class ImportController {
    private final ImportService imports;
    private final DatasetRepository datasets;
    ImportController(ImportService imports,DatasetRepository datasets) { this.imports=imports;this.datasets=datasets; }
    private String scope(String workspace) { return workspace==null?"demo-shop":workspace; }
    @PostMapping({"/demo/api/imports","/api/workspaces/{workspace}/imports"})
    ResponseEntity<?> upload(@PathVariable(required=false) String workspace,@RequestHeader(value="Idempotency-Key",required=false) String key,@RequestBody Map<String,Object> body) {
        return ResponseEntity.accepted().body(imports.upload(scope(workspace),key,body));
    }
    @GetMapping({"/demo/api/imports","/api/workspaces/{workspace}/imports"})
    Map<String,Object> list(@PathVariable(required=false) String workspace) { return Map.of("items",imports.list(scope(workspace))); }
    @GetMapping({"/demo/api/imports/{id}","/api/workspaces/{workspace}/imports/{id}"})
    Map<String,Object> get(@PathVariable(required=false) String workspace,@PathVariable String id) { return imports.get(scope(workspace),id); }
    @PostMapping({"/demo/api/imports/{id}/check","/api/workspaces/{workspace}/imports/{id}/check"})
    ResponseEntity<?> check(@PathVariable(required=false) String workspace,@PathVariable String id,@RequestBody Map<String,Object> body) {
        return ResponseEntity.accepted().body(imports.check(scope(workspace),id,body));
    }
    @PostMapping({"/demo/api/imports/{id}/confirm","/api/workspaces/{workspace}/imports/{id}/confirm"})
    Map<String,Object> confirm(@PathVariable(required=false) String workspace,@PathVariable String id,@RequestBody Map<String,Object> body) { return imports.confirm(scope(workspace),id,body); }
    @PostMapping({"/demo/api/imports/{id}/archive","/api/workspaces/{workspace}/imports/{id}/archive"})
    Map<String,Object> archive(@PathVariable(required=false) String workspace,@PathVariable String id,@RequestBody Map<String,Object> body) { return imports.archive(scope(workspace),id,body); }
    @GetMapping({"/demo/api/datasets","/api/workspaces/{workspace}/datasets"})
    Map<String,Object> datasets(@PathVariable(required=false) String workspace) { return Map.of("items",datasets.versions(scope(workspace))); }
    @GetMapping({"/demo/api/datasets/{id}","/api/workspaces/{workspace}/datasets/{id}"})
    Map<String,Object> dataset(@PathVariable(required=false) String workspace,@PathVariable String id) { return datasets.metadata(scope(workspace),id); }
}
