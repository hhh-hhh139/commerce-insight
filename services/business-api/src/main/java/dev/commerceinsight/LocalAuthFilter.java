package dev.commerceinsight;

import java.io.*;
import java.nio.file.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import jakarta.servlet.*;
import jakarta.servlet.http.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.web.filter.OncePerRequestFilter;

/** Bearer roles plus an explicitly enabled, loopback-only synthetic-data demo. */
@Component
@Order(Ordered.HIGHEST_PRECEDENCE+20)
class LocalAuthFilter extends OncePerRequestFilter {
    private final byte[] userToken, workerToken;
    private final boolean demoEnabled;
    private final OwnerAccess owner;
    LocalAuthFilter(@Value("${commerce.runtime}") String runtime,
            @Value("${commerce.demo.enabled:false}") boolean demoEnabled,OwnerAccess owner) throws IOException {
        this.demoEnabled=demoEnabled;this.owner=owner;
        userToken=read(Path.of(runtime,"user.token")); workerToken=read(Path.of(runtime,"worker.token"));
        if (MessageDigest.isEqual(userToken,workerToken)) throw new IllegalArgumentException("distinct_roles_required");
    }
    private byte[] read(Path path) throws IOException {
        String value=Files.readString(path).strip();
        if (!value.matches("[A-Za-z0-9_-]{32,128}")) throw new IllegalArgumentException("invalid_demo_token");
        return ("Bearer " + value).getBytes(StandardCharsets.UTF_8);
    }
    @Override
    protected void doFilterInternal(HttpServletRequest request,HttpServletResponse response,FilterChain chain) throws ServletException,IOException {
        String path=request.getRequestURI();
        if (path.equals("/health") && request.getMethod().equals("GET")) { chain.doFilter(request,response); return; }
        boolean ownerRoute=java.util.Set.of("/","/login","/login.css","/login.js","/session/csrf","/logout").contains(path);
        boolean demo=path.startsWith("/demo/");
        if(demo || ownerRoute) {
            response.setHeader("Cache-Control","no-store");
            response.setHeader("X-Content-Type-Options","nosniff");
            response.setHeader("Referrer-Policy","no-referrer");
            response.setHeader("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'");
        }
        if(ownerRoute) {
            if(!owner.enabled) { reject(response,404,"owner_access_disabled");return; }
            if(!owner.host.equals(request.getHeader("Host"))) { reject(response,403,"owner_host_required");return; }
            if(!request.getMethod().equals("GET")) {
                if(!owner.origin.equals(request.getHeader("Origin")) || !"same-origin".equals(request.getHeader("Sec-Fetch-Site"))) { reject(response,403,"same_origin_required");return; }
                if(request.getContentLengthLong()<0 || request.getContentLengthLong()>4096) { reject(response,413,"login_body_limit");return; }
            }
            if(path.equals("/login") && request.getMethod().equals("POST") && !owner.allowLogin()) { response.setHeader("Retry-After","60");reject(response,429,"login_rate_limited");return; }
            chain.doFilter(request,response);return; // Spring Security enforces login/logout and CSRF.
        }
        if (demo) {
            String host="127.0.0.1:"+request.getLocalPort();
            if (!demoEnabled) { reject(response,404,"demo_disabled"); return; }
            // No proxy trust. Host checking also prevents DNS rebinding to this listener.
            if(owner.enabled) {
                if(!owner.host.equals(request.getHeader("Host"))) { reject(response,403,"owner_host_required");return; }
            } else if (!"127.0.0.1".equals(request.getRemoteAddr()) || !"127.0.0.1".equals(request.getLocalAddr())
                    || !host.equals(request.getHeader("Host"))) { reject(response,403,"local_demo_only"); return; }
            boolean asset=java.util.Set.of("/demo/index.html","/demo/app.js","/demo/app.css","/demo/session.js","/demo/examples.json","/demo/imports.html","/demo/imports.js","/demo/imports.css").contains(path)
                || path.matches("/demo/import-samples/(standard-minor|renamed-major)/(orders|items|refunds)\\.csv");
            if (asset && request.getMethod().equals("GET")) { chain.doFilter(request,response); return; }
            String origin=request.getHeader("Origin");
            String expectedOrigin=owner.enabled?owner.origin:"http://"+host;
            if (!path.startsWith("/demo/api/") || !"1".equals(request.getHeader("X-Commerce-Demo"))
                    || !"same-origin".equals(request.getHeader("Sec-Fetch-Site"))
                    || (origin!=null && !origin.equals(expectedOrigin))
                    || (!request.getMethod().equals("GET") && !java.util.Objects.equals(origin,expectedOrigin))) {
                reject(response,403,"same_origin_required"); return;
            }
        }
        if(owner.enabled && !demo && (path.startsWith("/api/") || path.startsWith("/internal/"))
                && (!"127.0.0.1".equals(request.getRemoteAddr()) || !("127.0.0.1:"+request.getLocalPort()).equals(request.getHeader("Host")))) {
            reject(response,403,"service_loopback_required");return;
        }
        byte[] expected=path.startsWith("/api/") ? userToken : path.startsWith("/internal/") ? workerToken : null;
        String header=request.getHeader("Authorization");
        if (!demo && (expected==null || header==null || !MessageDigest.isEqual(expected,header.getBytes(StandardCharsets.UTF_8)))) {
            reject(response,401,"unauthorized"); return;
        }
        // Bound the JSON body before Jackson allocates its object tree.
        int limit=path.matches("/(demo/api|api/workspaces/[^/]+)/imports") ? 768000
            : path.matches("/internal/tasks/[^/]+/complete") ? 2000000 : 65536;
        byte[] body=request.getInputStream().readNBytes(limit+1);
        if (body.length>limit) { response.setStatus(413); return; }
        var wrapped=new HttpServletRequestWrapper(request) {
            @Override public ServletInputStream getInputStream() {
                var stream=new ByteArrayInputStream(body);
                return new ServletInputStream() {
                    public int read() { return stream.read(); }
                    public boolean isFinished() { return stream.available()==0; }
                    public boolean isReady() { return true; }
                    public void setReadListener(ReadListener listener) { throw new UnsupportedOperationException("sync_only"); }
                };
            }
            @Override public BufferedReader getReader() { return new BufferedReader(new InputStreamReader(getInputStream(),StandardCharsets.UTF_8)); }
        };
        chain.doFilter(wrapped,response);
    }
    private void reject(HttpServletResponse response,int status,String code) throws IOException {
        response.setStatus(status); response.setContentType("application/json");
        response.getWriter().write("{\"error\":\""+code+"\"}");
    }
}
