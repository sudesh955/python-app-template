from app.config import load_config
from app.context import AppContext


def hello():
  print("hello")


def hello_world():
  print("hello", "world")


def main():
  config = load_config("etc/config.toml")
  ctx = AppContext(config)
  print(ctx.config)
