package dev.commerceinsight;

import java.nio.charset.StandardCharsets;
import java.util.*;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

@Component
class JsonCodec {
    private final JsonMapper json;
    JsonCodec(JsonMapper json) { this.json=json; }
    private Object ordered(Object value) {
        if (value instanceof Map<?,?> map) {
            var sorted=new TreeMap<String,Object>(); map.forEach((k,v)->sorted.put(k.toString(),ordered(v)));return sorted;
        }
        if (value instanceof List<?> list) return list.stream().map(this::ordered).toList();
        return value;
    }
    String encode(Object value) { return json.writeValueAsString(ordered(value)); }
    @SuppressWarnings("unchecked") Map<String,Object> parse(String value) { return json.readValue(value,Map.class); }
    String digest(Object value) { return TaskService.sha(encode(value).getBytes(StandardCharsets.UTF_8)); }
}
