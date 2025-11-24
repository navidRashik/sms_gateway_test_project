FROM --platform=linux/arm64 node:18-alpine

WORKDIR /app

# Install curl for health checks
RUN apk add --no-cache curl

# Copy package files
COPY package*.json ./

# Install dependencies (including dev dependencies for tsx)
RUN npm ci

# Copy source code
COPY sms_gateway_provider.ts ./
COPY tsconfig.json ./

# Create non-root user
RUN addgroup -g 1001 -S nodejs && \
    adduser -S nodejs -u 1001

USER nodejs

# Expose port (will be overridden by docker-compose)
EXPOSE 8071

# Start the provider service
CMD ["npx", "tsx", "sms_gateway_provider.ts"]
