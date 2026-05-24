from app.config import AppConfig, load_config


class AppContext:
  config: AppConfig

  def __init__(self, config: AppConfig) -> None:
    self.config = config


def create_app_context(configfile: str = "etc/config.toml"):
  config = load_config(configfile)
  return AppContext(config)
