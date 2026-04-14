from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://orders:orders@localhost:5432/order_service"
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379

    # Base URL this service is reachable at (used as webhookUrl when calling payment-provider)
    SELF_BASE_URL: str = "http://localhost:8000"

    # payment-provider service base URL
    PAYMENT_PROVIDER_URL: str = "http://localhost:3000"

    # Merchant ID sent to payment-provider
    MERCHANT_ID: str = "order-service"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def redis_dsn(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}"


settings = Settings()
