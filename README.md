# python-app-template

A minimal Python application template with structured configuration loading, a
custom entry-point runner, and debugger support.

## Quickstart

```bash
pip install -r requirements.txt
mkdir etc
# create etc/config.toml and etc/env, then:
python exe.py app/main.py
```

## Structure

| Path | Purpose |
|---|---|
| `exe.py` | Entry point — imports and runs any module function via CLI args |
| `debug.py` | Launches `exe.py` with a debugpy server for remote debugging |
| `app/config.py` | TOML config loader with env-file support (`etc/env`) |
| `app/context.py` | `AppContext` class holding the loaded config |
| `app/main.py` | Default entry module called by `exe.py` |
| `app/types.py` | Forward-reference helpers (`AppContextT`) for type hints |
| `etc/` | Local-only directory (gitignored) for per-machine config/env |

## Usage

### exe.py

```bash
./exe.py <module_path> [function_name] [args...]
```

- `module_path` — dotted path to a `.py` file, e.g. `app/main.py`
- `function_name` — optional, defaults to `main`
- `args` — positional and `--key value` keyword arguments, auto-cast via type
  hints

Example:

```bash
python exe.py app/main.py
python exe.py app/config.py load_env
```

### etc/argv

Place a file at `etc/argv` to supply default CLI arguments. Lines starting
with `#` are ignored. Command-line arguments take precedence over the file.

### etc/env

Place a file at `etc/env` to set environment variables. Each line should be
`KEY=VALUE`. Called automatically by `load_config()`.

### Debugging

```bash
python debug.py
```

Starts a debugpy listener on `localhost:5678`. Attach your editor's debugger,
then execution proceeds as `exe.py` would.
