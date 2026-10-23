# Build stage
FROM golang:1.26-alpine AS builder

ENV GOTOOLCHAIN=local

# Set working directory
WORKDIR /app

# Install git and other dependencies
RUN apk add --no-cache git

# Copy go mod files
COPY go.mod go.sum ./

# Download dependencies
RUN go mod download

# Copy source code
COPY . .

# Build the application
RUN CGO_ENABLED=0 GOOS=linux go build -p=4 -trimpath -o main .

# Final stage
FROM alpine:3.23

# Install ca-certificates for HTTPS requests
RUN apk --no-cache add ca-certificates

# Create non-root user
RUN adduser -D -h /app -s /bin/sh appuser

WORKDIR /app

# Copy the binary from builder stage
COPY --from=builder --chown=appuser:appuser /app/main .

# Copy database migrations
COPY --from=builder --chown=appuser:appuser /app/database/migrations ./database/migrations

USER appuser

# Expose port
EXPOSE 8080

# Run the application
CMD ["./main"]
