import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from inspect import signature, Parameter
from typing import Any, Generic, Literal, Type, TypeVar, TypedDict

import msgspec

from app.context import create_app_context
from app.error import AppError
from app.message import decode_message, Struct
from app.types import AppContextT

Params = TypeVar("Params")
CmdToken = int | float | str | bool | None
CmdTokenType = TypeVar("CmdTokenType", bound=CmdToken)
HandlerFunction = TypeVar("HandlerFunction", bound=Callable)
CommandHandlerKind = Literal[
  "no-params", "with-context", "with-argv", "with-params"
]


class CommandHandleWithNoParams(TypedDict):
  kind: Literal["no-params"]
  fn: Callable[[], None]


class CommandHandlerWithContext(TypedDict):
  kind: Literal["with-context"]
  fn: Callable[[AppContextT], None]


class CommandHandlerWithArgv(TypedDict):
  kind: Literal["with-argv"]
  fn: Callable[["Argv"], None]


class CommandHandlerWithParams(TypedDict, Generic[Params]):
  kind: Literal["with-params"]
  fn: Callable[[AppContextT, Params], None]


CommandHandler = (
  CommandHandlerWithArgv
  | CommandHandlerWithParams
  | CommandHandleWithNoParams
  | CommandHandlerWithContext
)


@dataclass
class Argv:
  items: list[CmdToken]
  ctx: "AppContextT | None" = None

  _cursor = 0

  def get(self):
    if self._cursor < len(self.items):
      return self.items[self._cursor]
    return None

  def next(self, typ: Type[CmdTokenType]) -> CmdTokenType:
    value = self.get()
    if not isinstance(value, typ):
      raise AppError("invalid_cmd_type")
    self._cursor += 1
    return value

  def check(self, typ: Type[CmdTokenType]) -> tuple[CmdTokenType, bool]:
    value = self.get()
    if not isinstance(value, typ):
      return None, False  # type: ignore
    self._cursor += 1
    return value, True

  def read(self) -> CmdToken:
    value = self.get()
    self._cursor += 1
    return value

  def count(self):
    return len(self.items)

  def to_json(self):
    builder = CmdJsonBuilder(self.items, self._cursor)
    data = builder.build()
    self._cursor = builder.cursor
    return data


@dataclass
class Command(Generic[Params]):
  subcommands: dict[str, "Command"]
  handler: CommandHandler | None = None
  params_type: type[Params] | None = None

  def execute(self, argv: Argv):
    handler = self.handler
    if handler is None:
      return False
    if handler["kind"] == "no-params":
      handler["fn"]()
    elif handler["kind"] == "with-argv":
      handler["fn"](argv)
    elif handler["kind"] == "with-context":
      assert argv.ctx is not None
      handler["fn"](argv.ctx)
    else:
      assert self.params_type is not None
      try:
        params = decode_message(argv.to_json(), self.params_type)
      except msgspec.ValidationError as e:
        print(e)
        raise AppError("invalid-command-params")
      assert argv.ctx is not None
      handler["fn"](argv.ctx, params)
    return True


class MainCommandParams(Struct):
  config: str = "etc/config.toml"


@dataclass
class MainCommand(Command):
  def __init__(self):
    super().__init__({}, {"kind": "with-argv", "fn": MainCommand.main})

  @staticmethod
  def main(argv: Argv):
    params = decode_message(argv.to_json(), MainCommandParams)
    argv.ctx = create_app_context(params.config)


Main = MainCommand()


def cmd(*prefixes: str, argv: Type[Params] | None = None):
  assert len(prefixes) != 0, "name of command is required"

  def wrapper(fn: HandlerFunction) -> HandlerFunction:
    s = signature(fn)
    kind: CommandHandlerKind = "no-params"
    for index, param in enumerate(s.parameters.values()):
      assert param.kind == Parameter.POSITIONAL_OR_KEYWORD, (
        f"Parameter '{param.name}' is not supported"
      )
      annotation = param.annotation
      if annotation == Parameter.empty:
        raise RuntimeError("annotation is required")
      elif index >= 2:
        raise RuntimeError("invalid number of arguments")
      elif index == 0:
        if annotation == Argv:
          kind = "with-argv"
        elif annotation == AppContextT:
          kind = "with-context"
      elif index == 1:
        assert kind == "with-context"
        kind = "with-params"
    handler: CommandHandler = {"kind": kind, "fn": fn}  # type: ignore

    cmd = Main
    for prefix in prefixes:
      it = cmd.subcommands.get(prefix)
      if it is None:
        it = Command({}, params_type=argv)
        cmd.subcommands[prefix] = it
      cmd = it
    assert cmd.handler is None, f"{' '.join(prefixes)} is already registered"
    cmd.handler = handler
    return fn

  return wrapper


def execute(txt: list[str]) -> bool:
  if len(txt) != 0 and txt[0] not in Main.subcommands:
    return False
  tokens: list[CmdToken] = []
  for it in txt:
    tokens.extend(_split_tokens(it))
  command = Main
  argv = Argv(tokens)
  while True:
    command.execute(argv)
    cmd, ok = argv.check(str)
    if not ok:
      return True
    subcommand = command.subcommands.get(cmd)
    if subcommand is None:
      raise RuntimeError(f"subommand '{cmd}' not found")
    command = subcommand


class CmdJsonBuilder:
  def __init__(self, tokens: list[CmdToken], cursor: int = 0) -> None:
    self.equal = "="
    self.scoper = "."
    self.cursor = cursor
    self.tokens = tokens
    self.scope_suffix = ":"
    self.current_scope = ""
    self.length = len(tokens)
    self.array_placeholder = "$"
    self.values: dict[str, Any] = {"": {}}
    self.possible_lists: dict[str, bool] = {}

  def build(self):
    while self.cursor < self.length:
      key = self.tokens[self.cursor]
      self.cursor += 1
      if not isinstance(key, str):
        raise AppError("invalid-cmd-token-for-json")
      if key.endswith(self.scope_suffix):
        self._change_scope(key)
        continue
      elif not self._handle_key_value(key):
        self.cursor -= 1
        break
    self._build_lists()
    return self.values[""]

  def _handle_key_value(self, key: str) -> bool:
    if self.cursor >= self.length:
      return False
    if self.tokens[self.cursor] != self.equal:
      return False
    self.cursor += 1
    if self.cursor >= self.length:
      raise AppError("invalid-cmd-token-for-json-expected-value-after-key")
    value = self.tokens[self.cursor]
    self.cursor += 1
    if len(self.current_scope) != 0:
      key = self.current_scope + "." + key
    self._apply_key(key, value)
    return True

  def _change_scope(self, key: str):
    key = key[: -len(self.scope_suffix)]
    if key.endswith(self.array_placeholder):
      parts = key.split(self.scoper)
      scope = self._get_value_for_scope(parts)
      key = key[: -len(self.array_placeholder)] + str(len(scope))
    self.current_scope = key

  def _build_lists(self):
    keys = sorted(self.possible_lists.keys(), reverse=True)
    for key in keys:
      value = self.values[key]
      required = self.possible_lists[key]
      if self._can_be_valid_list(value):
        self._build_list(key, value)
      elif required:
        raise AppError("invalid-cmd-token-for-json")

  def _can_be_valid_list(self, value: dict[str, Any]):
    for i in range(len(value)):
      if str(i) not in value:
        return False
    return True

  def _build_list(self, key: str, value: dict[str, Any]):
    li = [value[str(i)] for i in range(len(value))]
    if len(key) == 0:
      self.values[""] = li
    else:
      parts = key.split(self.scoper)
      scope, key = self.scoper.join(parts[:-1]), parts[-1]
      self.values[scope][key] = [value[str(i)] for i in range(len(value))]

  def _add_to_lists(self, parts: list[str], required: bool):
    key = self.scoper.join(parts)
    value = self.possible_lists.get(key, False)
    self.possible_lists[key] = value or required

  def _apply_key(self, key: str, value: CmdToken):
    parts = key.split(self.scoper)
    scope = self._get_value_for_scope(parts)
    key = parts[-1]
    if key == self.array_placeholder:
      scope[str(len(scope))] = value
      self._add_to_lists(parts[:-1], True)
      self.possible_lists[self.scoper.join(parts[:-1])]
    else:
      scope[key] = value
      if key.isdigit():
        self._add_to_lists(parts[:-1], False)

  def _get_value_for_scope(self, parts: list[str]) -> dict[str, Any]:
    cursor = 0
    length = len(parts) - 1
    value = self.values[""]
    while cursor < length:
      part = parts[cursor]
      cursor += 1
      key = self.scoper.join(parts[:cursor])
      if part not in value:
        subvalue = {}
        value[part] = subvalue
        self.values[key] = subvalue
        if part.isdigit():
          # todo: verify this when cursor is 0
          self._add_to_lists(parts[: cursor - 1], False)
      else:
        subvalue = value[part]
        if not isinstance(subvalue, dict):
          raise AppError("invalid-cmd-token-for-json")
      value = subvalue
    return value


def _split_tokens(cmd: str) -> list[CmdToken]:
  """
  Split a string into tokens with automatic type conversion.

  Rules:
    - Double-quoted strings (e.g. "Hello World") are kept as a single string token.
    - Unquoted tokens that are valid integers become int.
    - Unquoted tokens that are valid floats become float.
    - Unquoted "true" / "false" become bool.
    - Unquoted "null" becomes None.
    - Everything else stays as str.

  Examples:
      >>> split_tokens("1 2 3")
      [1, 2, 3]
      >>> split_tokens("a b c")
      ['a', 'b', 'c']
      >>> split_tokens('"Hello World" 1 2 3')
      ['Hello World', 1, 2, 3]
      >>> split_tokens('"2"')
      ['2']
      >>> split_tokens("true")
      [True]
      >>> split_tokens("null")
      [None]
      >>> split_tokens("x=1")
      ['x', '=', 1]
      >>> split_tokens("x= 1")
      ['x', '=', 1]
      >>> split_tokens("x = 1")
      ['x', '=', 1]
      >>> split_tokens('x="hi"')
      ['x', '=', 'hi']
  """
  raw_tokens = re.findall(
    r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|=|[^\s=]+', cmd
  )

  result: list = []
  for token in raw_tokens:
    if token == "true":
      result.append(True)
    elif token == "false":
      result.append(False)
    elif token == "null":
      result.append(None)
    elif token.startswith('"') and token.endswith('"'):
      result.append(json.loads(token))
    else:
      try:
        result.append(int(token))
        continue
      except ValueError:
        pass
      try:
        result.append(float(token))
        continue
      except ValueError:
        pass
      result.append(token)

  return result


def cmd_to_json(txt: str):
  return CmdJsonBuilder(_split_tokens(txt)).build()


def test(filename: str):
  with open(filename, "r") as f:
    txt = f.read()
  print(cmd_to_json(txt))


def main(txt: str):
  print(cmd_to_json(txt))
  assert cmd_to_json("x=1 y=1") == {"x": 1, "y": 1}
  assert cmd_to_json("x.y=1") == {"x": {"y": 1}}
  assert cmd_to_json("x.0=1 x.1=1") == {"x": [1, 1]}
  assert cmd_to_json("x.$=1 x.$=1") == {"x": [1, 1]}
  assert cmd_to_json("x: $=1 $=1") == {"x": [1, 1]}
  assert cmd_to_json("0.x=1 0.y=2 1.x=3 1.y=4") == [
    {"x": 1, "y": 2},
    {"x": 3, "y": 4},
  ]
  assert cmd_to_json("0: x=1 y=2 1: x=3 y=4") == [
    {"x": 1, "y": 2},
    {"x": 3, "y": 4},
  ]
  assert cmd_to_json("$: x=1 y=2 $: x=3 y=4") == [
    {"x": 1, "y": 2},
    {"x": 3, "y": 4},
  ]
  assert cmd_to_json("a.$: x=1 y=2 a.$: x=3 y=4") == {
    "a": [
      {"x": 1, "y": 2},
      {"x": 3, "y": 4},
    ]
  }
