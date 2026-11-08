package dev.commerceinsight;

import java.util.Map;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
class Errors {
    @ExceptionHandler(ApiError.class)
    ResponseEntity<?> api(ApiError e) { return ResponseEntity.status(e.status).body(Map.of("error",e.getMessage())); }
    @ExceptionHandler(HttpMessageNotReadableException.class)
    ResponseEntity<?> json() { return ResponseEntity.badRequest().body(Map.of("error","invalid_json")); }
}
