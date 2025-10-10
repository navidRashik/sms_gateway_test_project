# Project: Twillow mvp copy cretion

## 3rd party apis list
- we have following apis for diffrent sms providers:

    ```bash
    curl --location 'http://localhost:8071/api/sms/provider1' \
      --header 'Content-Type: application/json' \
      --data '{
        "phone": "01921317475",
        "text": "klsfjdk"
      }'
    ```
    ```bash
    curl --location 'http://localhost:8072/api/sms/provider2' \
      --header 'Content-Type: application/json' \
      --data '{
        "phone": "01921317475",
        "text": "klsfjdk"
      }'
    ```
    ```bash
    curl --location 'http://localhost:8073/api/sms/provider3' \
      --header 'Content-Type: application/json' \
      --data '{
        "phone": "01921317475",
        "text": "klsfjdk"
      }'
    ```

- in all these 3rd party apis we have a rate limit of 50rps.
- NOTE: the providers run on distinct ports: 8071, 8072 and 8073 (not all on 8071).
  - When running locally (outside docker-compose) use the localhost examples above.
  - When running via docker-compose, other services should call providers by service name (for example: http://provider1:8071/api/sms/provider1) because Docker Compose provides internal DNS for service hostnames.

# requirements
- we need to be able to serve 200 rps so lets queue up the requests first and then send them to the 3rd party apis to fullfill the 200rps requirement.
- for queuing tool lets use redis.
- we will be using redis to calculate if we are hitting the rate limit of 50rps for each provider. we can use setx with expiry of 1 second to count the requests.

# development plan
- use docker compose to run all the services locally.
- user python as backend language.
- use fastapi as web framework.
- lets use taskiq with redis to queue up the requests for task queueing.
- lets use sqlite as database for storing the request and response data.
- redis for rate limiting and task queueing.

# issues to resolve
- most of the times 3rd party apis are down or not reachable. for handling that we will use retrying mechanism with exponential backoff and max retry count of 5. so we need to put a redis key with expiry of 5min for each provider that how many times they have failed and succeded in last 5min. If failed rate is 70% then we will not send any request to that provider for next 5min. for that we will keep a flag in redis and that flag will automatically expire after 5min. so that we can start sending requests to that provider again. When the flag is set we will do waited round robin between the other 2 providers. 
- if a request to a provider fails then we push it to a queue for retrying. and when we retrieve it from the retry queue we will check the db which provider it failed last time and we will skip that provider and do round robin between the other 2 providers given that both are healthy. if one is unhealthy then we will send it to the healthy provider. if both are unhealthy then we will put it back to the retry queue with a delay of 1min. and we will keep doing this until the request is successful or the request has been retried 5 times. after 5 times we will mark the request as failed and will not retry again. Also send it to a dead letter queue for manual inspection.
- 

## Architectural Constraints

- **NO BLOCKING DELAYS**: All retry delays and scheduling MUST use TaskIQ's built-in scheduler, not `asyncio.sleep()` or any other blocking operations
- **Worker Efficiency**: Task workers should never be blocked waiting - use `taskiq.schedule()` for delayed task execution
- **Exponential Backoff via Scheduling**: Implement exponential backoff by scheduling retry tasks for future execution timestamps
- **Non-blocking Retries**: When a task fails, immediately schedule the retry task with appropriate delay rather than sleeping in the current task
- **Resource Optimization**: Workers should be freed immediately after task completion to handle other queued tasks
- **REDIS RATE LIMITING PATTERN**: Use fixed keys with expiry, NOT timestamped keys. Pattern: `rate_limit:provider_id` with INCR + EXPIRE, not `rate_limit:provider_id:timestamp`
- **Counter Accumulation**: Rate limit counters must accumulate within the time window, use Redis INCR on consistent keys that expire after the window period
- **Key Persistence**: Rate limiting keys should persist for the entire window duration to properly track request counts within that window

## Provider Selection Strategy (Update)

- **Execution-time Provider Selection**: Do NOT choose provider at enqueue time. Enqueue a lightweight dispatch task with just the message payload. The worker selects the provider right before sending using current rate limits and health state.
- **Retries Re-dispatch**: On failure/timeouts, schedule a re-dispatch task (not the provider-specific send) with the failed provider added to an exclusion list to avoid immediate reuse.
- **Distribution Service**: Keep using SMSDistributionService for selection when available; if exclusions are provided, fall back to simple selection honoring exclusions.
