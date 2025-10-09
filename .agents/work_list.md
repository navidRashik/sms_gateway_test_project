# Work List: SMS Gateway Implementation

| id    | type    | title                              | assignee          | state | priority | created_at          | updated_at          |
|-------|---------|------------------------------------|-------------------|-------|----------|---------------------|---------------------|
| T-001 | chore   | Set up project structure and dependencies | orchestrator | ready | P0       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-002 | chore   | Create docker-compose configuration | orchestrator | ready | P0       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-003 | chore   | Set up database schema and migrations | orchestrator | done | P1       | 2025-10-09T12:40:28Z | 2025-10-09T13:01:01Z |
| T-004 | feature | Implement Redis rate limiting for providers | code | ready | P0       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-005 | feature | Implement taskiq task queueing system | code | ready | P0       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-006 | feature | Implement provider health tracking and failure detection | code | ready | P0       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-007 | feature | Implement retry mechanism with exponential backoff | code | blocked | P1       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-015 | bug | Fix blocking asyncio.sleep in retry logic | github-copilot | ready | P0       | 2025-10-09T12:59:00Z | 2025-10-09T12:59:00Z |
| T-016 | feature | Implement TaskIQ scheduling for retry delays | github-copilot | ready | P0       | 2025-10-09T12:59:00Z | 2025-10-09T12:59:00Z |
| T-017 | bug | Fix Redis rate limiter timestamped key architecture | github-copilot | done | P0       | 2025-10-09T13:15:00Z | 2025-10-09T13:15:00Z |
| T-018 | refactor | Move provider selection to execution time | github-copilot | done | P0 | 2025-10-10T00:00:00Z | 2025-10-10T00:00:00Z |
| T-008 | feature | Implement weighted round-robin distribution logic | code | ready | P1       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-009 | feature | Create FastAPI endpoints and middleware | code | ready | P0       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-010 | feature | Implement request/response data persistence | code | ready | P1       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |

## T-018 | refactor | Move provider selection to execution time

**description:** Refactor the task flow so provider selection occurs when the worker executes the task (dispatch time), not at enqueue time. Introduce a dispatch task that selects the provider using current rate/health/distribution state and then calls the send task. Update retries to re-dispatch with exclusion of failed provider.

**acceptance_criteria:**

- Given an incoming SMS request
- When it is enqueued
- Then the queued task contains only message payload (no provider chosen)
- And the worker selects the provider right before sending
- And retries schedule a re-dispatch excluding the failed provider

**assignee:** github-copilot
**state:** done
**priority:** P0
**created_at:** 2025-10-10T00:00:00Z
**updated_at:** 2025-10-10T00:00:00Z
| T-011 | test    | Write comprehensive unit tests | jest-test-engineer | ready | P1       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-011 | test    | Write integration tests for full workflow | jest-test-engineer | ready | P1       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-013 | docs    | Create developer guide and API documentation | orchestrator | ready | P2       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |
| T-014 | test    | Set up CI/CD pipeline with testing | orchestrator | ready | P2       | 2025-10-09T12:40:28Z | 2025-10-09T12:40:28Z |

---

## T-001 | chore | Set up project structure and dependencies
**description:** Initialize the project structure with proper directory layout, create essential configuration files, and set up all required dependencies for the SMS gateway service.

**acceptance_criteria:**
- Given a new project directory
- When the project structure is initialized
- Then a proper directory structure exists with src/, tests/, .agents/, docs/ folders
- And pyproject.toml contains all required dependencies (fastapi, taskiq, redis, sqlmodel, alembic, pytest, httpx)
- And requirements.txt is generated with locked versions
- And .env.example contains all required environment variables (Redis URL, Database URL, API keys)

**assignee:** orchestrator
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-002 | chore | Create docker-compose configuration
**description:** Set up docker-compose.yml to orchestrate all required services including Redis, SQLite database, and the FastAPI application for local development.

**acceptance_criteria:**
- Given the project root directory
- When docker-compose.yml is created
- Then Redis service is configured with proper ports and persistence
- And SQLite database service is configured (or database container if needed)
- And FastAPI application service is configured with proper environment variables
- And volume mounts are set up for database persistence and code hot-reloading
- And all services can start successfully with docker-compose up

**assignee:** orchestrator
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-003 | chore | Set up database schema and migrations
**description:** Design and implement database schema for storing SMS request/response data and provider health metrics, set up Alembic for database migrations.

**acceptance_criteria:**
- Given an empty SQLite database
- When database schema is initialized
- Then sms_requests table exists with fields: id, phone, text, status, provider_used, created_at, updated_at
- And sms_responses table exists with fields: id, request_id, response_data, status_code, created_at
- And provider_health table exists with fields: id, provider_name, success_count, failure_count, last_checked, is_healthy
- And Alembic migration files are created for schema versioning
- And initial migration can be applied successfully

**assignee:** orchestrator
**state:** ready
**priority:** P1
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-004 | feature | Implement Redis rate limiting for providers
**description:** Implement Redis-based rate limiting system to ensure each SMS provider doesn't exceed 50 RPS limit using sliding window technique.

**acceptance_criteria:**
- Given Redis is running and three SMS providers
- When multiple requests are sent simultaneously
- Then each provider receives maximum 50 requests per second
- And Redis SET operations with 1-second expiry are used for rate limiting counters
- And requests exceeding provider limits are queued for later processing
- And rate limiting works correctly under high load (200 RPS input)

**assignee:** code
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-005 | feature | Implement taskiq task queueing system
**description:** Set up TaskIQ with Redis broker to handle SMS request queuing and background processing for the SMS gateway service.

**acceptance_criteria:**
- Given TaskIQ is configured with Redis broker
- When SMS requests are received at high rate (200 RPS)
- Then requests are queued in Redis for background processing
- And task workers can process queued requests asynchronously
- And taskiq broker handles connection pooling and error recovery
- And failed tasks are properly handled and logged

**assignee:** code
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-006 | feature | Implement provider health tracking and failure detection
**description:** Implement health monitoring system that tracks provider success/failure rates over 5-minute windows and marks providers as unhealthy when failure rate exceeds 70%.

**acceptance_criteria:**
- Given three SMS providers with varying response times
- When requests are processed over time
- Then success and failure counts are tracked per provider in Redis
- And 5-minute sliding windows are used for health calculation
- And providers with >70% failure rate are marked unhealthy for 5 minutes
- And Redis keys expire automatically after 5 minutes
- And health status is checked before routing requests to providers

**assignee:** code
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-007 | feature | Implement retry mechanism with exponential backoff
**description:** Implement retry logic with exponential backoff for failed SMS requests, including max retry count of 5 and smart provider selection for retries.

**acceptance_criteria:**
- Given a failed SMS request to a provider
- When retry mechanism is triggered
- Then request is retried up to 5 times with exponential backoff delays
- And failed provider is skipped in subsequent retry attempts
- And round-robin distribution is used among healthy providers
- And failed requests are persisted in database with retry status
- And requests exceeding max retries are marked as permanently failed

**assignee:** code
**state:** ready
**priority:** P1
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-008 | feature | Implement weighted round-robin distribution logic
**description:** Implement intelligent request distribution that uses weighted round-robin when providers are healthy and fallback logic when providers become unhealthy.

**acceptance_criteria:**
- Given three SMS providers with different health statuses
- When requests need to be distributed
- Then weighted round-robin is used when all providers are healthy
- And requests are distributed only to healthy providers when some are unhealthy
- And equal distribution is maintained among available healthy providers
- And provider health status is checked before each distribution decision
- And distribution logic handles single healthy provider scenarios

**assignee:** code
**state:** ready
**priority:** P1
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-009 | feature | Create FastAPI endpoints and middleware
**description:** Implement FastAPI application with SMS endpoints, middleware for request processing, and integration with all the implemented services.

**acceptance_criteria:**
- Given a FastAPI application structure
- When SMS endpoints are implemented
- Then POST /api/sms endpoint accepts phone and text parameters
- And middleware handles request queuing and provider distribution
- And proper error handling and logging is implemented
- And OpenAPI documentation is auto-generated
- And CORS middleware is configured for cross-origin requests

**assignee:** code
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-010 | feature | Implement request/response data persistence
**description:** Implement database persistence layer for storing SMS requests, responses, and provider interaction data using SQLModel.

**acceptance_criteria:**
- Given SMS requests and responses
- When data persistence is implemented
- Then all SMS requests are stored in database with unique IDs
- And provider responses are linked to original requests
- And request status is tracked (pending, processing, completed, failed)
- And database queries support filtering by status, provider, and time range
- And data integrity is maintained during concurrent operations

**assignee:** code
**state:** ready
**priority:** P1
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-011 | test | Write comprehensive unit tests
**description:** Write unit tests for all core components including rate limiting, health tracking, retry logic, and distribution algorithms.

**acceptance_criteria:**
- Given all implemented components
- When unit tests are written
- Then rate limiting logic has 100% test coverage
- And provider health tracking has comprehensive test scenarios
- And retry mechanism with exponential backoff is fully tested
- And weighted round-robin distribution is tested with various provider states
- And all tests pass with minimum 90% code coverage

**assignee:** jest-test-engineer
**state:** ready
**priority:** P1
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-012 | test | Write integration tests for full workflow
**description:** Write integration tests that verify the complete SMS processing workflow from request to response.

**acceptance_criteria:**
- Given the complete SMS gateway system
- When integration tests are executed
- Then end-to-end SMS request flow is tested
- And Redis rate limiting integration is verified
- And TaskIQ task processing is tested
- And database persistence is validated
- And error scenarios and retry logic are covered

**assignee:** jest-test-engineer
**state:** ready
**priority:** P1
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-013 | docs | Create developer guide and API documentation
**description:** Create comprehensive documentation including developer setup guide, API documentation, and architecture overview.

**acceptance_criteria:**
- Given the implemented SMS gateway system
- When documentation is created
- Then developer setup guide includes all installation and configuration steps
- And API documentation covers all endpoints with examples
- And architecture overview explains system components and data flow
- And troubleshooting guide addresses common issues
- And README.md provides clear project overview and getting started instructions

**assignee:** orchestrator
**state:** ready
**priority:** P2
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-014 | test | Set up CI/CD pipeline with testing
**description:** Configure CI/CD pipeline with automated testing, linting, and deployment for the SMS gateway service.

**acceptance_criteria:**
- Given the project repository
- When CI/CD pipeline is configured
- Then GitHub Actions workflow runs on every push and PR
- And automated tests execute with coverage reporting
- And linting and formatting checks are enforced
- And Docker image is built and tested
- And deployment to staging environment is automated

**assignee:** orchestrator
**state:** ready
**priority:** P2
**created_at:** 2025-10-09T12:40:28Z
**updated_at:** 2025-10-09T12:40:28Z

---

## T-015 | bug | Fix blocking asyncio.sleep in retry logic
**description:** Remove all asyncio.sleep() calls from tasks.py that block workers during retry delays. These blocking calls prevent workers from processing other tasks and reduce system throughput.

**acceptance_criteria:**
- Given the current send_sms_to_provider task with asyncio.sleep calls
- When the blocking sleep calls are removed
- Then no asyncio.sleep() calls exist in the retry logic (lines 92, 108, 127)
- And workers are freed immediately after task failure instead of blocking
- And retry scheduling is delegated to TaskIQ's built-in scheduler
- And system can process more tasks concurrently during retry delays

**assignee:** github-copilot
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:59:00Z
**updated_at:** 2025-10-09T12:59:00Z
**blocked_by:** []
**comments:** 
- 2025-10-09T12:59:00Z | github-copilot | CRITICAL: Current asyncio.sleep calls in retry logic block workers and reduce throughput

---

## T-016 | feature | Implement TaskIQ scheduling for retry delays
**description:** Implement proper TaskIQ scheduling for retry delays using taskiq.schedule() instead of blocking operations. This ensures workers remain free to process other tasks while retries are scheduled for future execution.

**acceptance_criteria:**
- Given a failed SMS request that needs retry
- When retry mechanism is triggered
- Then taskiq.schedule() is used to schedule retry tasks with exponential backoff delays
- And current task completes immediately without blocking
- And scheduled retry tasks execute at the correct future timestamp
- And exponential backoff calculation remains the same (2^retry_count)
- And max retry count of 5 is still enforced

**assignee:** github-copilot
**state:** ready
**priority:** P0
**created_at:** 2025-10-09T12:59:00Z
**updated_at:** 2025-10-09T12:59:00Z
**blocked_by:** [T-015]
**comments:**
- 2025-10-09T12:59:00Z | github-copilot | Must implement non-blocking retry scheduling using TaskIQ's scheduler

---

## T-017 | bug | Fix Redis rate limiter timestamped key architecture
**description:** Fix fundamental flaw in rate limiter where new Redis keys are created every second with timestamps, preventing proper rate limiting. Rate limiter should use consistent keys that expire after the window period.

**acceptance_criteria:**
- Given the current rate limiter implementation with timestamped keys
- When rate limiter key generation is fixed
- Then Redis keys use format "rate_limit:provider_id" without timestamps
- And keys expire after the window duration (1 second) using Redis EXPIRE
- And INCR operations accumulate on the same key during the window
- And rate limiting works correctly with proper counter accumulation
- And both provider-specific and global rate limiters are fixed

**assignee:** github-copilot
**state:** done
**priority:** P0
**created_at:** 2025-10-09T13:15:00Z
**updated_at:** 2025-10-09T13:15:00Z
**comments:**
- 2025-10-09T13:15:00Z | github-copilot | CRITICAL: Timestamped keys prevent counter accumulation within time windows
- 2025-10-09T13:15:00Z | github-copilot | Fixed key generation to use consistent keys with proper expiry