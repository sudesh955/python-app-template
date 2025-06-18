from app.config import load_config
from app.context import AppContext


def main():
  config = load_config("etc/config.toml")
  ctx = AppContext(config)
  print(ctx.config)
