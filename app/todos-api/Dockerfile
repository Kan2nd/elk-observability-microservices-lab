FROM node:20-alpine

EXPOSE 8082

WORKDIR /usr/src/app

COPY package.json ./
RUN npm install

COPY . .

# Use --require to auto-require APM without touching app code
CMD ["node", "--require", "elastic-apm-node/start", "server.js"]
