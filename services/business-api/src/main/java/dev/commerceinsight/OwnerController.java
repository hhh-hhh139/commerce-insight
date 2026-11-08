package dev.commerceinsight;

import java.util.Map;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.ResponseEntity;
import org.springframework.security.web.csrf.CsrfToken;
import org.springframework.web.bind.annotation.*;

@RestController
@ConditionalOnProperty(name="commerce.owner.enabled",havingValue="true")
class OwnerController {
    @GetMapping(value="/login",produces="text/html")
    ClassPathResource login() { return new ClassPathResource("static/login.html"); }
    @GetMapping("/")
    ResponseEntity<Void> root() { return ResponseEntity.status(302).header("Location","/demo/index.html").build(); }
    @GetMapping("/session/csrf")
    Map<String,Object> csrf(CsrfToken token) {
        return Map.of("header_name",token.getHeaderName(),"parameter_name",token.getParameterName(),"token",token.getToken());
    }
}
