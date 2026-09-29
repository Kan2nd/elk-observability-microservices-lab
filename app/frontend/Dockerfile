FROM node:20-alpine

RUN apk add --no-cache python3 make g++

EXPOSE 8080

WORKDIR /usr/src/app

COPY package.json ./
RUN npm install --legacy-peer-deps --ignore-scripts

COPY . .

CMD ["sh", "-c", "npm start" ]