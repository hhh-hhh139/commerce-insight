package dev.commerceinsight;

import java.nio.file.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.*;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.core.userdetails.*;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.config.http.SessionCreationPolicy;

@Configuration
class OwnerSecurity {
    @Bean
    UserDetailsService users(OwnerAccess owner,@Value("${commerce.runtime}") String runtime) throws Exception {
        if(!owner.enabled)return new InMemoryUserDetailsManager(); // No generated password or hidden default account.
        String hash=Files.readString(Path.of(runtime,"owner.hash")).strip();
        if(!hash.matches("\\{bcrypt}\\$2[aby]\\$12\\$[./A-Za-z0-9]{53}"))throw new IllegalArgumentException("invalid_owner_password_hash");
        return new InMemoryUserDetailsManager(User.withUsername("owner").password(hash).roles("OWNER").build());
    }
    @Bean
    SecurityFilterChain security(HttpSecurity http,OwnerAccess owner) throws Exception {
        http.requestCache(c->c.disable()).httpBasic(c->c.disable());
        if(!owner.enabled) {
            http.authorizeHttpRequests(a->a.anyRequest().permitAll()).csrf(c->c.disable())
                .formLogin(c->c.disable()).logout(c->c.disable()).sessionManagement(s->s.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
        } else {
            http.authorizeHttpRequests(a->a.requestMatchers("/login","/login.css","/login.js","/session/csrf","/health","/").permitAll()
                    .requestMatchers("/demo/**").hasRole("OWNER").anyRequest().permitAll())
                .csrf(c->c.ignoringRequestMatchers("/api/**","/internal/**"))
                .formLogin(f->f.loginPage("/login").loginProcessingUrl("/login")
                    .successHandler((request,response,auth)->{response.setStatus(302);response.setHeader("Location","/demo/index.html");})
                    .failureHandler((request,response,error)->{response.setStatus(302);response.setHeader("Location","/login?error");}))
                .logout(l->l.logoutUrl("/logout").logoutSuccessHandler((request,response,auth)->{response.setStatus(302);response.setHeader("Location","/login?logout");})
                    .invalidateHttpSession(true).deleteCookies("JSESSIONID"))
                .sessionManagement(s->s.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED))
                .exceptionHandling(e->e.authenticationEntryPoint((request,response,error)->{
                    if(request.getRequestURI().startsWith("/demo/api/")) {
                        response.setStatus(401);response.setContentType("application/json");response.getWriter().write("{\"error\":\"login_required\"}");
                    } else {response.setStatus(302);response.setHeader("Location","/login");}
                }).accessDeniedHandler((request,response,error)->{
                    var auth=org.springframework.security.core.context.SecurityContextHolder.getContext().getAuthentication();
                    if(request.getRequestURI().startsWith("/demo/api/") && (auth==null || auth instanceof org.springframework.security.authentication.AnonymousAuthenticationToken)) {
                        response.setStatus(401);response.setContentType("application/json");response.getWriter().write("{\"error\":\"login_required\"}");return;
                    }
                    response.setStatus(403);response.setContentType("application/json");response.getWriter().write("{\"error\":\"session_request_rejected\"}");
                }));
        }
        return http.build();
    }
}
