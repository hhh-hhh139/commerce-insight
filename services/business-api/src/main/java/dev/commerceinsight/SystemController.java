package dev.commerceinsight;

import java.util.Map;
import org.springframework.web.bind.annotation.*;

@RestController
class SystemController {
    private final ResourceControls controls;
    SystemController(ResourceControls controls) { this.controls=controls; }
    @GetMapping("/api/workspaces/{workspace}/system")
    Map<String,Object> status(@PathVariable String workspace) { return controls.status(workspace); }
    @GetMapping("/demo/api/system")
    Map<String,Object> demoStatus() { return controls.status("demo-shop"); }
}
