# Rip Tool Rewrite: Python TUI Implementation Plan

## Overview

Rewrite the existing 815-line Bash ripping script into a modern Python application with a beautiful terminal UI, intelligent metadata lookup, and optimized performance.

## Goals

1. **Modern TUI** - Beautiful, responsive terminal interface using Textual
2. **Speed** - Async I/O, parallel operations where beneficial
3. **Smart Title Detection** - TMDb integration with fuzzy matching
4. **Seamless Organization** - Auto-classify extras, match TV episodes by duration

## Existing Libraries to Leverage

- **[python-makemkv](https://pypi.org/project/makemkv/)** - Python wrapper for makemkvcon with cleaner API
- **[auto-makemkv](https://github.com/sturgeon1/auto-makemkv)** - Reference for TMDb matching patterns
- **[mnamer](https://pypi.org/project/mnamer/)** - Media renaming with TMDb/TVDb integration
- **[mapi](https://github.com/jkwill87/mapi)** - High-level metadata API for TMDb/TVDb/OMDb

## Tech Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| TUI Framework | **Textual** | Modern async design, built-in widgets (DataTable, ProgressBar), CSS styling, reactive state |
| CLI Framework | **Typer** | Clean API, auto-generates help, integrates with Rich |
| MakeMKV Wrapper | **python-makemkv** | Already handles parsing, cleaner than raw subprocess |
| Metadata | **TMDb API** via aiohttp | Most comprehensive, free tier available |
| Fuzzy Matching | **RapidFuzz** | Fast, accurate, MIT licensed |
| Config | **Pydantic Settings** | Validation, env var support, TOML loading |

## Project Structure

```
/home/jacob/code/rip/
├── pyproject.toml
├── README.md
├── rip                         # Legacy bash script (reference)
├── src/
│   └── ripper/
│       ├── __init__.py
│       ├── __main__.py         # Entry point
│       ├── cli.py              # Typer CLI
│       │
│       ├── config/
│       │   ├── __init__.py
│       │   └── settings.py     # Pydantic settings
│       │
│       ├── core/
│       │   ├── __init__.py
│       │   ├── disc.py         # Data structures (Title, DiscInfo, etc.)
│       │   ├── scanner.py      # Disc scanning (wraps python-makemkv)
│       │   ├── ripper.py       # Ripping engine with progress
│       │   └── organizer.py    # File organization for Emby
│       │
│       ├── metadata/
│       │   ├── __init__.py
│       │   ├── tmdb.py         # TMDb API client
│       │   ├── matcher.py      # Fuzzy title matching
│       │   └── classifier.py   # Movie/TV detection, extras classification
│       │
│       ├── tui/
│       │   ├── __init__.py
│       │   ├── app.py          # Main Textual App
│       │   ├── screens/
│       │   │   ├── __init__.py
│       │   │   ├── main.py     # Main menu
│       │   │   ├── scan.py     # Disc scan results
│       │   │   ├── rip.py      # Ripping progress
│       │   │   └── organize.py # Extras classification
│       │   ├── widgets/
│       │   │   ├── __init__.py
│       │   │   ├── title_table.py
│       │   │   └── rip_progress.py
│       │   └── styles/
│       │       └── app.tcss
│       │
│       └── utils/
│           ├── __init__.py
│           ├── formatting.py   # Duration/size formatting
│           └── drive.py        # Drive detection, eject
│
├── tests/
│   ├── conftest.py
│   ├── test_scanner.py
│   ├── test_matcher.py
│   └── test_organizer.py
│
└── config/
    └── ripper.example.toml
```

## Key Data Structures

```python
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

class MediaType(Enum):
    MOVIE = auto()
    TV_SHOW = auto()
    UNKNOWN = auto()

class ExtraType(Enum):
    EXTRAS = "extras"
    BEHIND_THE_SCENES = "behind the scenes"
    DELETED_SCENES = "deleted scenes"
    FEATURETTES = "featurettes"
    INTERVIEWS = "interviews"
    SCENES = "scenes"
    SHORTS = "shorts"
    TRAILERS = "trailers"

@dataclass
class Title:
    id: int
    name: str
    duration_seconds: int
    size_bytes: int
    chapter_count: int
    is_main_feature: bool = False
    suggested_extra_type: Optional[ExtraType] = None
    matched_episode: Optional[tuple[int, int]] = None  # (season, episode)

@dataclass
class DiscInfo:
    name: str
    device: str
    titles: list[Title]
    detected_media_type: MediaType = MediaType.UNKNOWN
    tmdb_id: Optional[int] = None
    tmdb_title: Optional[str] = None
    year: Optional[int] = None
```

## Configuration

```toml
# ~/.config/ripper/config.toml

[metadata]
tmdb_api_key = ""  # or RIPPER_TMDB_API_KEY env var
auto_lookup = true
fuzzy_threshold = 75

[paths]
staging_dir = "/mnt/media/Rips-Staging"
movies_dir = "/mnt/media/Movies"
tv_dir = "/mnt/media/TV"

[device]
path = "/dev/sr0"
auto_eject = true

[ripping]
min_main_length = 3600    # 1 hour - classify as main feature
min_extra_length = 30     # Skip titles < 30s (menus)

[ui]
theme = "dark"
```

## Smart Features

### 1. Intelligent Title Detection

```
Disc Name: "DUNE_PART_TWO_DISC_1"
    │
    ▼ Clean & normalize
"Dune Part Two"
    │
    ▼ TMDb search
[Results: "Dune: Part Two (2024)", "Dune (2021)", ...]
    │
    ▼ RapidFuzz WRatio matching (threshold: 75)
Match: "Dune: Part Two (2024)" (score: 92)
    │
    ▼ Fetch runtime for validation
Runtime: 166 min → Matches longest title (2h 46m) ✓
```

### 2. Extras Auto-Classification

Pattern matching on title names:
- "Behind the Scenes" / "Making of" / "BTS" → `behind the scenes/`
- "Deleted" / "Extended Scene" → `deleted scenes/`
- "Featurette" / "Documentary" → `featurettes/`
- "Interview" / "Q&A" → `interviews/`
- "Trailer" / "Teaser" → `trailers/`
- Everything else → `extras/`

### 3. TV Episode Matching

```python
# Match disc titles to episodes by duration
def match_episodes(titles: list[Title], episodes: list[Episode]) -> dict:
    # Use Hungarian algorithm for optimal assignment
    # Allow ±2 minute tolerance for credits variations
    pass
```

### 4. Multi-Disc State Persistence

```python
@dataclass
class RipSession:
    session_id: str
    movie_name: str
    total_discs: int
    completed_discs: list[int]
    ripped_files: dict[int, list[Path]]

    # Saved to ~/.cache/ripper/sessions/{id}.json
    # Allows resuming interrupted multi-disc rips
```

## TUI Screens

### Main Menu
```
┌─────────────────────────────────────────────────────────────┐
│  🎬 Ripper                                          [?]Help │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   Disc: DUNE_PART_TWO (4K UHD)                             │
│   Detected: Dune: Part Two (2024) - Movie                  │
│                                                             │
│   ┌─────────────────────────────────────────────────────┐  │
│   │ [1] Rip Movie with Extras                           │  │
│   │ [2] Rip Main Feature Only                           │  │
│   │ [3] Rip Multi-Disc Movie                            │  │
│   │ [4] Rip TV Episodes                                 │  │
│   │ [5] Select Specific Titles                          │  │
│   │ [6] View Disc Info                                  │  │
│   └─────────────────────────────────────────────────────┘  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ [S]can  [E]ject  [Q]uit                                    │
└─────────────────────────────────────────────────────────────┘
```

### Title Selection
```
┌─────────────────────────────────────────────────────────────┐
│  Select Titles                                    [Space]  │
├─────────────────────────────────────────────────────────────┤
│ ┌─┬────┬──────────────────────────┬─────────┬──────┬─────┐ │
│ │☑│ ID │ Name                     │Duration │ Size │ Ch. │ │
│ ├─┼────┼──────────────────────────┼─────────┼──────┼─────┤ │
│ │☑│  0 │ Dune: Part Two           │ 2:46:06 │32.1GB│  18 │ │
│ │☐│  1 │ Behind the Scenes        │   42:15 │ 4.2GB│   8 │ │
│ │☐│  2 │ Deleted Scenes           │   18:30 │ 1.8GB│   5 │ │
│ │☐│  3 │ Trailer                  │    2:30 │ 0.3GB│   1 │ │
│ └─┴────┴──────────────────────────┴─────────┴──────┴─────┘ │
├─────────────────────────────────────────────────────────────┤
│ [Enter] Rip Selected  [A]ll  [N]one  [Esc] Back            │
└─────────────────────────────────────────────────────────────┘
```

### Ripping Progress
```
┌─────────────────────────────────────────────────────────────┐
│  Ripping: Dune: Part Two (2024)                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Title 0: Dune: Part Two                                   │
│  ████████████████████████░░░░░░░░░░░░░░░░  62%  18.2GB     │
│  ETA: 12:34                                                 │
│                                                             │
│  ──────────────────────────────────────────────────────    │
│                                                             │
│  Completed:                                                 │
│  ✓ Title 1: Behind the Scenes (4.2GB)                      │
│  ✓ Title 2: Deleted Scenes (1.8GB)                         │
│                                                             │
│  Queue:                                                     │
│  ○ Title 3: Trailer                                        │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│ [C]ancel                                                    │
└─────────────────────────────────────────────────────────────┘
```

## CLI Interface

```bash
# Interactive TUI (default)
rip

# Single movie with extras
rip movie "Dune: Part Two (2024)"

# Main feature only
rip movie "Dune: Part Two (2024)" --no-extras

# Multi-disc
rip multi "Lord of the Rings Extended" --discs 2

# TV episodes
rip tv "Breaking Bad" --season 1

# Info only
rip info

# Headless mode (for automation)
rip movie "Movie Name" --headless --auto-organize
```

## Implementation Phases

### Phase 1: Core Engine
- [ ] Project setup (pyproject.toml, structure)
- [ ] Config system with Pydantic
- [ ] Disc scanning using python-makemkv
- [ ] Basic ripping with progress callbacks
- [ ] File organization for Emby

### Phase 2: Metadata Integration
- [ ] TMDb API client (async)
- [ ] Fuzzy title matching with RapidFuzz
- [ ] Movie/TV content detection
- [ ] Extras auto-classification

### Phase 3: TUI Development
- [ ] Textual app shell with screens
- [ ] Main menu screen
- [ ] Title selection with DataTable
- [ ] Ripping progress with ProgressBar
- [ ] Organization/extras screen

### Phase 4: Advanced Features
- [ ] TV episode matching by duration
- [ ] Multi-disc session persistence
- [ ] Settings screen
- [ ] Error recovery and logging

### Phase 5: Polish
- [ ] Comprehensive error handling
- [ ] Unit tests
- [ ] Documentation
- [ ] Headless/automation mode

## Dependencies

```toml
[project]
name = "ripper"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = [
    "textual>=0.50.0",
    "typer>=0.9.0",
    "makemkv>=0.3.0",
    "aiohttp>=3.9.0",
    "rapidfuzz>=3.6.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
]

[project.scripts]
rip = "ripper.cli:app"

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "textual-dev>=1.0.0",
    "ruff>=0.1.0",
]
```

## Notes

- The optical drive is the I/O bottleneck - parallel ripping of multiple titles from one disc has limited benefit
- Parallelism helps most with: metadata lookup during scan, file moves during rip, multi-drive setups
- python-makemkv handles the makemkvcon parsing complexity - no need to reimplement
- TMDb API free tier: 1000 requests/day, sufficient for personal use
