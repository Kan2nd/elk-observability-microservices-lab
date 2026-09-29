FROM maven:3.9-eclipse-temurin-11

EXPOSE 8083

# Install curl (needed to download APM agent - minimal image doesn't include it)
RUN apt-get update && apt-get install -y curl --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

# Download APM agent during Docker build
RUN mkdir -p /opt/elastic && \
    curl -L https://repo1.maven.org/maven2/co/elastic/apm/elastic-apm-agent/1.48.0/elastic-apm-agent-1.48.0.jar \
    -o /opt/elastic/elastic-apm-agent.jar

WORKDIR /usr/src/app
COPY pom.xml ./
COPY . .
RUN mvn clean install -DskipTests=true

# Run with javaagent
ENTRYPOINT ["java", \
  "-javaagent:/opt/elastic/elastic-apm-agent.jar", \
  "-jar", "./target/users-api-0.0.1-SNAPSHOT.jar"]
