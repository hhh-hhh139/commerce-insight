package dev.commerceinsight;

import java.io.*;
import java.nio.charset.StandardCharsets;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;

/** Operator CLI: password only over stdin; output is captured into a private hash file. */
public class PasswordHash {
    public static void main(String[] args) throws Exception {
        String password=new BufferedReader(new InputStreamReader(System.in,StandardCharsets.UTF_8)).readLine();
        if(password==null || password.getBytes(StandardCharsets.UTF_8).length<16 || password.getBytes(StandardCharsets.UTF_8).length>72)
            throw new IllegalArgumentException("password_requires_16_to_72_utf8_bytes");
        System.out.println("{bcrypt}"+new BCryptPasswordEncoder(12).encode(password));
    }
}
