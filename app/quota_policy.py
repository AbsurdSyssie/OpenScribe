"""Shared finite quota defaults and validation ceilings."""

DEFAULT_DAILY_TOKEN_LIMIT = 100_000
DEFAULT_MONTHLY_TOKEN_LIMIT = 1_000_000
DEFAULT_DAILY_AUDIO_SECONDS_LIMIT = 28_800
DEFAULT_MONTHLY_AUDIO_SECONDS_LIMIT = 259_200

MAX_DAILY_TOKENS = 10_000_000
MAX_MONTHLY_TOKENS = 100_000_000
MAX_DAILY_AUDIO_SECONDS = 24 * 3600
MAX_MONTHLY_AUDIO_SECONDS = 1_000 * 3600

TEAM_QUOTA_DEFAULTS = {
    "default_daily_token_limit": DEFAULT_DAILY_TOKEN_LIMIT,
    "default_monthly_token_limit": DEFAULT_MONTHLY_TOKEN_LIMIT,
    "default_daily_audio_seconds_limit": DEFAULT_DAILY_AUDIO_SECONDS_LIMIT,
    "default_monthly_audio_seconds_limit": DEFAULT_MONTHLY_AUDIO_SECONDS_LIMIT,
}

TEAM_QUOTA_LIMITS = {
    "default_daily_token_limit": MAX_DAILY_TOKENS,
    "default_monthly_token_limit": MAX_MONTHLY_TOKENS,
    "default_daily_audio_seconds_limit": MAX_DAILY_AUDIO_SECONDS,
    "default_monthly_audio_seconds_limit": MAX_MONTHLY_AUDIO_SECONDS,
}
