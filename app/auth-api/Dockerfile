FROM golang:1.21-alpine

EXPOSE 8081

WORKDIR /app
RUN apk --no-cache add git

COPY . .
RUN go env -w GOPROXY=https://proxy.golang.org,direct GONOSUMDB=*
RUN go mod tidy

RUN go build -o auth-api .

CMD ["./auth-api"]

