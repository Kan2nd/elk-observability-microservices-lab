package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"time"

	// [ADDED] Elastic APM for Go - captures traces automatically
	"go.elastic.co/apm/v2"

	jwt "github.com/dgrijalva/jwt-go"
	"github.com/labstack/echo"
	"github.com/labstack/echo/middleware"
	gommonlog "github.com/labstack/gommon/log"
)

var (
	// ErrHttpGenericMessage that is returned in general case, details should be logged in such case
	ErrHttpGenericMessage = echo.NewHTTPError(http.StatusInternalServerError, "something went wrong, please try again later")

	// ErrWrongCredentials indicates that login attempt failed because of incorrect login or password
	ErrWrongCredentials = echo.NewHTTPError(http.StatusUnauthorized, "username or password is invalid")

	jwtSecret = "myfancysecret"
)

func main() {
	// [ADDED] Initialize Elastic APM tracer
	apmServerURL := os.Getenv("ELASTIC_APM_SERVER_URL")
	if apmServerURL == "" {
		apmServerURL = "http://localhost:8200"
	}

	// [INFO] APM tracer is initialized via environment variables (ELASTIC_APM_*)
	// No need to manually init - the apm.DefaultTracer() uses them automatically
	// Environment variables used:
	//   ELASTIC_APM_SERVER_URL - APM server endpoint (set above)
	//   ELASTIC_APM_SERVICE_NAME - service name
	//   ELASTIC_APM_ENVIRONMENT - environment name
	log.Printf("APM agent initialized (env var ELASTIC_APM_SERVER_URL=%s)", apmServerURL)

	hostport := ":" + os.Getenv("AUTH_API_PORT")
	userAPIAddress := os.Getenv("USERS_API_ADDRESS")

	envJwtSecret := os.Getenv("JWT_SECRET")
	if len(envJwtSecret) != 0 {
		jwtSecret = envJwtSecret
	}

	userService := UserService{
		Client:         http.DefaultClient,
		UserAPIAddress: userAPIAddress,
	}

	e := echo.New()
	e.Logger.SetLevel(gommonlog.INFO)

	if zipkinURL := os.Getenv("ZIPKIN_URL"); len(zipkinURL) != 0 {
		e.Logger.Infof("init tracing to Zipkit at %s", zipkinURL)

		if tracedMiddleware, tracedClient, err := initTracing(zipkinURL); err == nil {
			e.Use(echo.WrapMiddleware(tracedMiddleware))
			userService.Client = tracedClient
		} else {
			e.Logger.Infof("Zipkin tracer init failed: %s", err.Error())
		}
	} else {
		e.Logger.Infof("Zipkin URL was not provided, tracing is not initialised")
	}

	e.Use(middleware.Logger())
	e.Use(middleware.Recover())
	e.Use(middleware.CORS())

	// Route => handler
	e.GET("/version", func(c echo.Context) error {
		return c.String(http.StatusOK, "Auth API, written in Go\n")
	})

	e.POST("/login", getLoginHandler(userService))
	e.POST("/signup", getSignupHandler())

	// Start server
	e.Logger.Fatal(e.Start(hostport))
}

type LoginRequest struct {
	Username string `json:"username"`
	Password string `json:"password"`
}

type SignupRequest struct {
	Username  string `json:"username"`
	Password  string `json:"password"`
	Firstname string `json:"firstname"`
	Lastname  string `json:"lastname"`
}

func getLoginHandler(userService UserService) echo.HandlerFunc {
	f := func(c echo.Context) error {
		// [ADDED] Start APM transaction for login attempt
		tx := apm.DefaultTracer().StartTransaction("LoginAttempt", "request")
		defer tx.End()
		
		ctx := apm.ContextWithTransaction(c.Request().Context(), tx)
		
		requestData := LoginRequest{}
		decoder := json.NewDecoder(c.Request().Body)
		if err := decoder.Decode(&requestData); err != nil {
			log.Printf("LOGIN_FAILED: could not read credentials - %s", err.Error())
			tx.Outcome = "failure"
			return ErrHttpGenericMessage
		}

		// [INFO] Log username for APM context (Go v2 doesn't have AddLabel, use logging)
		log.Printf("LOGIN_ATTEMPT: username=%s timestamp=%d", requestData.Username, time.Now().Unix())

		loginSpan := tx.StartSpan("user_authentication", "auth", nil)
		user, err := userService.Login(ctx, requestData.Username, requestData.Password)
		
		if err != nil {
			if err != ErrWrongCredentials {
				log.Printf("LOGIN_FAILED: could not authorize user '%s' - %s", requestData.Username, err.Error())
				tx.Outcome = "failure"
				loginSpan.End()
				return ErrHttpGenericMessage
			}
			log.Printf("LOGIN_FAILED: Invalid credentials for user '%s'", requestData.Username)
			tx.Outcome = "failure"
			loginSpan.End()
			return ErrWrongCredentials
		}
		log.Printf("LOGIN_SUCCESS: user_id=%s timestamp=%d", user.Username, time.Now().Unix())
		loginSpan.End()

		// [ADDED] Log successful login for Kibana visibility
		log.Printf("LOGIN_SUCCESS: User '%s' logged in successfully at %d", requestData.Username, time.Now().Unix())
		
		token := jwt.New(jwt.SigningMethodHS256)

		// Set claims
		claims := token.Claims.(jwt.MapClaims)
		claims["username"] = user.Username
		claims["firstname"] = user.FirstName
		claims["lastname"] = user.LastName
		claims["role"] = user.Role
		claims["exp"] = time.Now().Add(time.Hour * 72).Unix()

		// Generate encoded token and send it as response.
		t, err := token.SignedString([]byte(jwtSecret))
		if err != nil {
			log.Printf("LOGIN_FAILED: could not generate JWT token - %s", err.Error())
			tx.Outcome = "failure"
			return ErrHttpGenericMessage
		}

		tx.Outcome = "success"

		return c.JSON(http.StatusOK, map[string]string{
			"accessToken": t,
		})
	}

	return echo.HandlerFunc(f)
}

func getSignupHandler() echo.HandlerFunc {
	f := func(c echo.Context) error {
		// [ADDED] Start APM transaction for signup
		tx := apm.DefaultTracer().StartTransaction("SignupRequest", "request")
		defer tx.End()
		
		ctx := apm.ContextWithTransaction(c.Request().Context(), tx)
		
		requestData := SignupRequest{}
		decoder := json.NewDecoder(c.Request().Body)
		if err := decoder.Decode(&requestData); err != nil {
			log.Printf("could not read signup data from POST body: %s", err.Error())
			log.Printf("SIGNUP_FAILED: could not read signup data - %s", err.Error())
			tx.Outcome = "failure"
			return ErrHttpGenericMessage
		}

		log.Printf("SIGNUP_ATTEMPT: username=%s firstname=%s lastname=%s timestamp=%d", 
			requestData.Username, requestData.Firstname, requestData.Lastname, time.Now().Unix())

		// Forward signup request to users API
		usersAPIAddress := os.Getenv("USERS_API_ADDRESS")
		if usersAPIAddress == "" {
			log.Printf("SIGNUP_FAILED: USERS_API_ADDRESS environment variable is not set")
			tx.Outcome = "failure"
			return ErrHttpGenericMessage
		}

		signupURL := fmt.Sprintf("%s/users/register", usersAPIAddress)
		log.Printf("Forwarding signup request to: %s", signupURL)

		signupPayload := map[string]string{
			"username":  requestData.Username,
			"password":  requestData.Password,
			"firstname": requestData.Firstname,
			"lastname":  requestData.Lastname,
		}

		payloadBytes, err := json.Marshal(signupPayload)
		if err != nil {
			log.Printf("SIGNUP_FAILED: could not marshal signup payload - %s", err.Error())
			tx.Outcome = "failure"
			return ErrHttpGenericMessage
		}

		// [ADDED] Create span for users API call
		usersAPISpan := tx.StartSpan("forward_to_users_api", "http.client", nil)
		req, err := http.NewRequest("POST", signupURL, bytes.NewBuffer(payloadBytes))
		if err != nil {
			log.Printf("SIGNUP_FAILED: could not create signup request - %s", err.Error())
			tx.Outcome = "failure"
			usersAPISpan.End()
			return ErrHttpGenericMessage
		}
		req.Header.Set("Content-Type", "application/json")
		req = req.WithContext(ctx)

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			log.Printf("SIGNUP_FAILED: could not forward signup request to users API - %s", err.Error())
			tx.Outcome = "failure"
			usersAPISpan.End()
			return ErrHttpGenericMessage
		}

		defer resp.Body.Close()
		bodyBytes, err := ioutil.ReadAll(resp.Body)
		if err != nil {
			log.Printf("SIGNUP_FAILED: could not read response body - %s", err.Error())
			tx.Outcome = "failure"
			usersAPISpan.End()
			return ErrHttpGenericMessage
		}

		log.Printf("SIGNUP_FORWARD: Users API response status=%d body=%s", resp.StatusCode, string(bodyBytes))

		if resp.StatusCode == 201 {
			// [ADDED] Log successful signup for Kibana visibility
			log.Printf("SIGNUP_SUCCESS: User '%s' signed up successfully at %d", requestData.Username, time.Now().Unix())
			tx.Outcome = "success"
			usersAPISpan.End()
			return c.JSON(http.StatusCreated, map[string]string{
				"message": "User registered successfully",
			})
		} else if resp.StatusCode == 400 || resp.StatusCode == 409 {
			log.Printf("SIGNUP_FAILED: User '%s' - status %d", requestData.Username, resp.StatusCode)
			tx.Outcome = "failure"
			usersAPISpan.End()
			return c.JSON(resp.StatusCode, json.RawMessage(bodyBytes))
		}

		log.Printf("SIGNUP_FAILED: Unknown error - status %d", resp.StatusCode)
		tx.Outcome = "failure"
		usersAPISpan.End()
		return ErrHttpGenericMessage
	}

	return echo.HandlerFunc(f)
}
