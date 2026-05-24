from app.config import load_config
from app.context import AppContext
from app.cmd import cmd


@cmd("hello")
def hello():
  print("hello")


@cmd("hello", "world")
def hello_world():
  print("hello", "world")


def main():
  config = load_config("etc/config.toml")
  ctx = AppContext(config)
  print(ctx.config)
