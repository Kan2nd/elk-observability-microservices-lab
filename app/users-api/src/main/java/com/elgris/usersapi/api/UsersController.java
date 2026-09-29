package com.elgris.usersapi.api;

import com.elgris.usersapi.models.User;
import com.elgris.usersapi.models.UserRole;
import com.elgris.usersapi.repository.UserRepository;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import org.mindrot.jbcrypt.BCrypt;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.RedisTemplate;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.*;

// [ADDED] Elastic APM imports for custom instrumentation
import co.elastic.apm.api.ElasticApm;
import co.elastic.apm.api.Transaction;
import co.elastic.apm.api.Span;
import co.elastic.apm.api.Outcome;

import javax.servlet.http.HttpServletRequest;
import java.util.HashMap;
import java.util.LinkedList;
import java.util.List;
import java.util.Map;
import java.util.Date;

@RestController()
@RequestMapping("/users")
public class UsersController {
    // [ADDED] Logger for Kibana visibility
    private static final Logger logger = LoggerFactory.getLogger(UsersController.class);
    
    // [ADDED] Redis client for publishing signup events (same as todos-api)
    @Autowired(required = false)
    private RedisTemplate<String, String> redisTemplate;
    
    @Value("${redis.channel:log_channel}")
    private String logChannel;
    
    @Value("${jwt.secret}")
    private String jwtSecret;
    
    private ObjectMapper objectMapper = new ObjectMapper();

    @Autowired
    private UserRepository userRepository;


    @RequestMapping(value = "/", method = RequestMethod.GET)
    public List<User> getUsers() {
        List<User> response = new LinkedList<>();
        userRepository.findAll().forEach(response::add);

        return response;
    }

    @RequestMapping(value = "/{username}",  method = RequestMethod.GET)
    public User getUser(HttpServletRequest request, @PathVariable("username") String username) {

        Object requestAttribute = request.getAttribute("claims");
        if((requestAttribute == null) || !(requestAttribute instanceof Claims)){
            throw new RuntimeException("Did not receive required data from JWT token");
        }

        Claims claims = (Claims) requestAttribute;

        if (!username.equalsIgnoreCase((String)claims.get("username"))) {
            throw new AccessDeniedException("No access for requested entity");
        }

        return userRepository.findOneByUsername(username);
    }

    @RequestMapping(value = "/register", method = RequestMethod.POST)
    public ResponseEntity<?> registerUser(@RequestBody SignUpRequest signUpRequest) {
        // [ADDED] Start APM transaction for signup
        Transaction transaction = ElasticApm.startTransaction();
        transaction.setName("UserRegistration");
        transaction.setType("request");
        transaction.setLabel("username", signUpRequest.getUsername());
        transaction.setLabel("endpoint", "/users/register");
        transaction.setLabel("timestamp", System.currentTimeMillis());
        transaction.setLabel("firstname", signUpRequest.getFirstname() != null ? signUpRequest.getFirstname() : "");
        transaction.setLabel("lastname", signUpRequest.getLastname() != null ? signUpRequest.getLastname() : "");
        
        try {
            // Validate input
            if (signUpRequest.getUsername() == null || signUpRequest.getUsername().trim().isEmpty()) {
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "empty_username");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("SIGNUP_FAILED: Empty username");
                Map<String, String> error = new HashMap<>();
                error.put("message", "Username is required");
                return ResponseEntity.badRequest().body(error);
            }

            if (signUpRequest.getPassword() == null || signUpRequest.getPassword().length() < 4) {
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "weak_password");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("SIGNUP_FAILED: username={} - weak password", signUpRequest.getUsername());
                Map<String, String> error = new HashMap<>();
                error.put("message", "Password must be at least 4 characters");
                return ResponseEntity.badRequest().body(error);
            }

            // Check if user already exists
            Span checkSpan = transaction.startSpan("check_user_exists", "db", "query");
            checkSpan.setLabel("username", signUpRequest.getUsername());
            
            if (userRepository.findOneByUsername(signUpRequest.getUsername()) != null) {
                checkSpan.end();
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "user_already_exists");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("SIGNUP_FAILED: username={} - user already exists", signUpRequest.getUsername());
                Map<String, String> error = new HashMap<>();
                error.put("message", "Username already exists");
                return ResponseEntity.status(HttpStatus.CONFLICT).body(error);
            }
            checkSpan.end();

            // Hash password
            Span hashSpan = transaction.startSpan("hash_password", "crypto", "bcrypt");
            String hashedPassword = BCrypt.hashpw(signUpRequest.getPassword(), BCrypt.gensalt());
            hashSpan.end();

            // Create new user
            User newUser = new User();
            newUser.setUsername(signUpRequest.getUsername());
            newUser.setFirstname(signUpRequest.getFirstname() != null ? signUpRequest.getFirstname() : "");
            newUser.setLastname(signUpRequest.getLastname() != null ? signUpRequest.getLastname() : "");
            newUser.setPassword(hashedPassword);
            newUser.setRole(UserRole.USER);  // Default role is USER (0)

            // Save to database
            Span saveSpan = transaction.startSpan("save_user", "db", "save");
            saveSpan.setLabel("table", "users");
            saveSpan.setLabel("username", newUser.getUsername());
            userRepository.save(newUser);
            saveSpan.end();

            transaction.setLabel("username_created", newUser.getUsername());
            transaction.setLabel("success", true);

            // [ADDED] Publish signup event to Redis (same way as todos-api)
            Span redisSpan = transaction.startSpan("publish_to_redis", "cache", "redis");
            redisSpan.setLabel("channel", logChannel);
            try {
                if (redisTemplate != null) {
                    Map<String, Object> signupEvent = new HashMap<>();
                    signupEvent.put("opName", "SIGNUP");
                    signupEvent.put("username", newUser.getUsername());
                    Map<String, String> userData = new HashMap<>();
                    userData.put("username", newUser.getUsername());
                    userData.put("firstname", newUser.getFirstname());
                    userData.put("lastname", newUser.getLastname());
                    signupEvent.put("user", userData);
                    String eventJson = objectMapper.writeValueAsString(signupEvent);
                    redisTemplate.convertAndSend(logChannel, eventJson);
                    redisSpan.setLabel("success", true);
                    logger.info("SIGNUP_EVENT_PUBLISHED: username={}", newUser.getUsername());
                }
            } catch (Exception e) {
                redisSpan.setLabel("error", e.getMessage());
                logger.error("Error publishing signup to Redis: {}", e.getMessage());
            }
            redisSpan.end();

            transaction.setOutcome(Outcome.SUCCESS);
            logger.info("SIGNUP_SUCCESS: username={} firstname={} lastname={} timestamp={}", 
                newUser.getUsername(), newUser.getFirstname(), newUser.getLastname(), System.currentTimeMillis());

            Map<String, String> response = new HashMap<>();
            response.put("message", "User registered successfully");
            return ResponseEntity.status(HttpStatus.CREATED).body(response);
            
        } finally {
            transaction.end();
        }
    }

    @RequestMapping(value = "/login", method = RequestMethod.POST)
    public ResponseEntity<?> loginUser(@RequestBody LoginRequest loginRequest) {
        // [ADDED] Start APM transaction for login
        Transaction transaction = ElasticApm.startTransaction();
        transaction.setName("UserLogin");
        transaction.setType("request");
        transaction.setLabel("username", loginRequest.getUsername() != null ? loginRequest.getUsername() : "");
        transaction.setLabel("endpoint", "/users/login");
        transaction.setLabel("timestamp", System.currentTimeMillis());
        
        try {
            // Validate input
            if (loginRequest.getUsername() == null || loginRequest.getUsername().trim().isEmpty()) {
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "empty_username");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("LOGIN_FAILED: Empty username");
                Map<String, String> error = new HashMap<>();
                error.put("message", "Username is required");
                return ResponseEntity.badRequest().body(error);
            }

            if (loginRequest.getPassword() == null || loginRequest.getPassword().isEmpty()) {
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "empty_password");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("LOGIN_FAILED: username={} - empty password", loginRequest.getUsername());
                Map<String, String> error = new HashMap<>();
                error.put("message", "Password is required");
                return ResponseEntity.badRequest().body(error);
            }

            // Find user in database
            Span findSpan = transaction.startSpan("find_user", "db", "query");
            findSpan.setLabel("username", loginRequest.getUsername());
            
            User user = userRepository.findOneByUsername(loginRequest.getUsername());
            findSpan.end();
            
            if (user == null) {
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "user_not_found");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("LOGIN_FAILED: username={} - user not found", loginRequest.getUsername());
                Map<String, String> error = new HashMap<>();
                error.put("message", "Invalid credentials");
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(error);
            }

            // Verify password
            Span verifySpan = transaction.startSpan("verify_password", "crypto", "bcrypt");
            verifySpan.setLabel("username", user.getUsername());
            boolean passwordValid = BCrypt.checkpw(loginRequest.getPassword(), user.getPassword());
            verifySpan.end();
            
            if (!passwordValid) {
                transaction.setLabel("success", false);
                transaction.setLabel("failure_reason", "invalid_password");
                transaction.setOutcome(Outcome.FAILURE);
                logger.warn("LOGIN_FAILED: username={} - invalid password", loginRequest.getUsername());
                Map<String, String> error = new HashMap<>();
                error.put("message", "Invalid credentials");
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(error);
            }

            // Generate JWT token
            Span tokenSpan = transaction.startSpan("generate_jwt_token", "auth", "jwt");
            tokenSpan.setLabel("username", user.getUsername());
            
            String token = Jwts.builder()
                    .setSubject(user.getUsername())
                    .claim("username", user.getUsername())
                    .claim("user_key", user.getUsername())
                    .claim("role", user.getRole())
                    .setIssuedAt(new Date())
                    .setExpiration(new Date(System.currentTimeMillis() + 86400000)) // 24 hours
                    .signWith(SignatureAlgorithm.HS512, jwtSecret.getBytes())
                    .compact();
            
            tokenSpan.end();

            transaction.setLabel("username_logged_in", user.getUsername());
            transaction.setLabel("user_username", user.getUsername());
            transaction.setLabel("success", true);
            transaction.setOutcome(Outcome.SUCCESS);

            // Publish login event to Redis
            Span redisSpan = transaction.startSpan("publish_to_redis", "cache", "redis");
            redisSpan.setLabel("channel", logChannel);
            try {
                if (redisTemplate != null) {
                    Map<String, Object> loginEvent = new HashMap<>();
                    loginEvent.put("opName", "LOGIN");
                    loginEvent.put("username", user.getUsername());
                    Map<String, Object> userData = new HashMap<>();
                    userData.put("username", user.getUsername());
                    userData.put("firstname", user.getFirstname());
                    userData.put("lastname", user.getLastname());
                    userData.put("role", user.getRole());
                    loginEvent.put("user", userData);
                    loginEvent.put("timestamp", System.currentTimeMillis());
                    String eventJson = objectMapper.writeValueAsString(loginEvent);
                    redisTemplate.convertAndSend(logChannel, eventJson);
                    redisSpan.setLabel("success", true);
                    logger.info("LOGIN_EVENT_PUBLISHED: username={}", user.getUsername());
                }
            } catch (Exception e) {
                redisSpan.setLabel("error", e.getMessage());
                logger.error("Error publishing login to Redis: {}", e.getMessage());
            }
            redisSpan.end();

            logger.info("LOGIN_SUCCESS: username={} firstname={} lastname={} timestamp={}", 
                user.getUsername(), user.getFirstname(), user.getLastname(), System.currentTimeMillis());

            Map<String, Object> response = new HashMap<>();
            response.put("message", "Login successful");
            response.put("token", token);
            response.put("username", user.getUsername());
            response.put("firstname", user.getFirstname());
            response.put("lastname", user.getLastname());
            return ResponseEntity.ok(response);
            
        } finally {
            transaction.end();
        }
    }

    // Inner class for signup request
    public static class SignUpRequest {
        private String username;
        private String password;
        private String firstname;
        private String lastname;

        public String getUsername() {
            return username;
        }

        public void setUsername(String username) {
            this.username = username;
        }

        public String getPassword() {
            return password;
        }

        public void setPassword(String password) {
            this.password = password;
        }

        public String getFirstname() {
            return firstname;
        }

        public void setFirstname(String firstname) {
            this.firstname = firstname;
        }

        public String getLastname() {
            return lastname;
        }

        public void setLastname(String lastname) {
            this.lastname = lastname;
        }
    }

    // Inner class for login request
    public static class LoginRequest {
        private String username;
        private String password;

        public String getUsername() {
            return username;
        }

        public void setUsername(String username) {
            this.username = username;
        }

        public String getPassword() {
            return password;
        }

        public void setPassword(String password) {
            this.password = password;
        }
    }

}
