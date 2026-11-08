package dev.commerceinsight;

import java.net.URI;
import java.util.ArrayDeque;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/** One explicit external origin and one bounded login window; never trust proxy identity headers. */
@Component
class OwnerAccess {
    final boolean enabled;
    final String origin,host;
    private final int loginLimit;
    private final ArrayDeque<Long> attempts=new ArrayDeque<>();
    OwnerAccess(@Value("${commerce.owner.enabled:false}") boolean enabled,
            @Value("${commerce.owner.origin:}") String origin,
            @Value("${commerce.demo.enabled:false}") boolean demo,
            @Value("${server.servlet.session.cookie.secure:false}") boolean secureCookie,
            @Value("${commerce.owner.login-per-minute:10}") int loginLimit) {
        this.enabled=enabled;this.origin=origin;this.loginLimit=loginLimit;
        if(loginLimit<1 || loginLimit>60)throw new IllegalArgumentException("invalid_login_limit");
        if(!enabled) { host="";return; }
        try {
            URI uri=URI.create(origin);
            boolean local="http".equals(uri.getScheme()) && "127.0.0.1".equals(uri.getHost()) && uri.getPort()>0;
            boolean https="https".equals(uri.getScheme()) && uri.getHost()!=null && secureCookie;
            if(!demo || (!local && !https) || uri.getRawUserInfo()!=null || uri.getRawQuery()!=null
                    || uri.getRawFragment()!=null || !uri.getRawPath().isEmpty() || !origin.equals(uri.toASCIIString()))
                throw new IllegalArgumentException();
            host=uri.getRawAuthority();
        } catch(IllegalArgumentException e) { throw new IllegalArgumentException("invalid_owner_origin_or_cookie_configuration"); }
    }
    synchronized boolean allowLogin() {
        long now=System.nanoTime();
        while(!attempts.isEmpty() && now-attempts.getFirst()>=60_000_000_000L)attempts.removeFirst();
        if(attempts.size()>=loginLimit)return false;
        attempts.addLast(now);return true;
    }
}
