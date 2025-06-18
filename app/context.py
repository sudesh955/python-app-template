from app.config import AppConfig


class AppContext:
  config: AppConfig

  def __init__(self, config: AppConfig) -> None:
    self.config = config
