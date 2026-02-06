# rip

A 4K Blu-ray ripping utility for Emby media servers.

Rips discs via `makemkvcon`, organizes output into Emby-compatible folder structures, and supports movies, multi-disc movies, and TV shows. Includes TMDb integration for metadata lookup and fuzzy title matching.

Running `rip` with no arguments launches an interactive inline CLI that scans the disc, shows a menu, and walks you through the rip with inline progress.

## Prerequisites

- [MakeMKV](https://www.makemkv.com/) installed with `makemkvcon` on your `PATH`
- [uv](https://docs.astral.sh/uv/) package manager
- Python 3.11+
- A Blu-ray/UHD drive

## Install

Install `rip` globally so it's available on your `PATH`:

```sh
uv tool install -e /path/to/rip-tui
```

Or from the repo directory:

```sh
uv tool install -e .
```

This puts a `rip` command in `~/.local/bin/` (make sure that's on your `PATH`).

To update after pulling changes:

```sh
uv tool install --force -e .
```

## Usage

### Interactive mode

Launch the interactive CLI by running `rip` with no arguments:

```sh
rip
```

### CLI commands

```sh
# Rip a single-disc movie (all titles + extras)
rip movie "Dune (2021)"

# Rip main feature only, skip extras
rip movie "Dune (2021)" --no-extras

# Rip a multi-disc movie (prompts for disc swaps)
rip multi "Lawrence of Arabia (1962)" --discs 2

# Rip a TV show disc
rip tv "Seinfeld" 3

# Show disc title info without ripping
rip info

# Eject the disc
rip eject
```

Run `rip --help` or `rip <command> --help` for full option details.

## Configuration

Config is loaded from (in priority order):

1. Environment variables prefixed with `RIPPER_` (e.g. `RIPPER_TMDB_API_KEY`)
2. TOML file at `~/.config/ripper/config.toml`

Copy the example config to get started:

```sh
mkdir -p ~/.config/ripper
cp config/ripper.example.toml ~/.config/ripper/config.toml
```

### Settings

| Setting | Env var | Default | Description |
|---|---|---|---|
| `tmdb_api_key` | `RIPPER_TMDB_API_KEY` | `""` | TMDb API key for metadata lookup |
| `auto_lookup` | `RIPPER_AUTO_LOOKUP` | `true` | Auto-search TMDb for titles |
| `fuzzy_threshold` | `RIPPER_FUZZY_THRESHOLD` | `75` | Fuzzy match score threshold (0-100) |
| `staging_dir` | `RIPPER_STAGING_DIR` | `/mnt/media/Rips-Staging` | Temp directory for ripped files |
| `movies_dir` | `RIPPER_MOVIES_DIR` | `/mnt/media/Movies` | Emby movies library path |
| `tv_dir` | `RIPPER_TV_DIR` | `/mnt/media/TV` | Emby TV library path |
| `device` | `RIPPER_DEVICE` | `/dev/sr0` | Optical drive device path |
| `auto_eject` | `RIPPER_AUTO_EJECT` | `true` | Eject disc after ripping |
| `min_main_length` | `RIPPER_MIN_MAIN_LENGTH` | `3600` | Seconds threshold for main feature |
| `min_extra_length` | `RIPPER_MIN_EXTRA_LENGTH` | `30` | Skip titles shorter than this |

## Development

### Setup

```sh
git clone <repo-url>
cd rip-tui
uv sync --dev
```

### Run from source

```sh
uv run rip
```

### Tests

```sh
uv run pytest
```

### Lint and format

```sh
uv run ruff check .
uv run ruff format .
```

### Type check

```sh
uv run pyright
```

## Project structure

```
src/ripper/
  cli.py              # Typer CLI entry point
  __main__.py          # python -m ripper support
  config/settings.py   # Pydantic settings (env + TOML)
  core/
    disc.py            # Disc data models
    ripper.py          # MakeMKV ripping logic
    scanner.py         # Disc scanning
    organizer.py       # Emby folder organization
  metadata/
    classifier.py      # Title classification (main vs extras)
    matcher.py         # Fuzzy title matching
    tmdb.py            # TMDb API client
  tui/
    app.py             # Interactive CLI (inline prompts + progress)
  utils/
    formatting.py      # Display formatting helpers
    drive.py           # Drive/eject utilities
```
