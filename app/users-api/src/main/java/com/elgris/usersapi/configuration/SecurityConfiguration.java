package com.elgris.usersapi.configuration;

import com.elgris.usersapi.security.JwtAuthenticationFilter;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableGlobalMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configuration.WebSecurityConfigurerAdapter;
import org.springframework.security.web.authentication.www.BasicAuthenticationFilter;

@EnableWebSecurity
@EnableGlobalMethodSecurity(securedEnabled = true)
class HttpSecurityConfiguration {

    @Configuration
    public static class ApiConfigurerAdatper extends WebSecurityConfigurerAdapter {

        @Autowired
        private JwtAuthenticationFilter jwtAuthenticationFilter;

        @Override
        protected void configure(HttpSecurity http) throws Exception {
            // For stateless REST APIs using JWT tokens:
            // - CSRF is disabled (JWT tokens in Authorization header, not cookies)
            // - Authentication is handled entirely by JwtAuthenticationFilter
            // - The filter allows /users/register without authentication
            // - All other endpoints require valid JWT via the filter
            http.antMatcher("/**")
                    .csrf().disable()
                    .addFilterAfter(jwtAuthenticationFilter, BasicAuthenticationFilter.class);
        }
    }
}
