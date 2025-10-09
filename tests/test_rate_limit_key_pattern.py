import pytest
import asyncio

from src.rate_limiter import RateLimiter

@pytest.mark.asyncio
async def test_rate_limiter_uses_fixed_key_and_incr_expire(mock_redis):
    """
    Instantiate RateLimiter with window=1 and provider_id 'provider1'.
    Call is_allowed twice and assert Redis.INCR called with identical fixed key
    and EXPIRE called only once. Also assert no timestamp suffix in the key.
    """
    # Make incr return 1 on first call and 2 on second call
    mock_redis.incr.side_effect = [1, 2]

    rl = RateLimiter(redis_client=mock_redis, rate_limit=50, window=1)

    # First call
    allowed1, count1 = await rl.is_allowed("provider1")
    # Second call in same window
    allowed2, count2 = await rl.is_allowed("provider1")

    # Assert Redis.incr was called twice with identical key
    assert mock_redis.incr.call_count == 2
    calls = mock_redis.incr.call_args_list
    key_call_1 = calls[0][0][0]
    key_call_2 = calls[1][0][0]
    assert key_call_1 == "rate_limit:provider1"
    assert key_call_2 == "rate_limit:provider1"
    # Ensure no timestamp suffix (key should equal exactly the fixed pattern)
    assert ":" in key_call_1  # pattern includes provider separator
    assert key_call_1 == "rate_limit:provider1"

    # Expire should be called only once (on first increment == 1)
    mock_redis.expire.assert_called_once_with("rate_limit:provider1", 1)

    # Validate returned counts and allowed flags reflect side_effects
    assert count1 == 1
    assert count2 == 2
    assert allowed1 is True
    assert allowed2 is True


@pytest.mark.asyncio
async def test_get_current_count_uses_fixed_key(mock_redis):
    """
    Call get_current_count and assert it queries Redis with the fixed key.
    """
    mock_redis.get.return_value = b"3"
    rl = RateLimiter(redis_client=mock_redis, rate_limit=50, window=1)

    count = await rl.get_current_count("provider1")

    mock_redis.get.assert_called_once_with("rate_limit:provider1")
    assert count == 3