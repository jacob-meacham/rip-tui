"""Main menu screen with disc info and action selection."""

import asyncio
import logging

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Static,
)

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, MediaType
from ripper.core.scanner import scan_disc
from ripper.metadata.classifier import (
    classify_titles,
    detect_media_type,
)
from ripper.metadata.matcher import clean_disc_name
from ripper.tui.screens.disc_info import DiscInfoScreen
from ripper.tui.screens.rip import RipScreen
from ripper.tui.screens.scan import ScanScreen

logger = logging.getLogger(__name__)


class MainScreen(Screen):
    """Main menu with disc info and rip options."""

    BINDINGS = [
        Binding("s", "scan", "Scan"),
        Binding("e", "eject", "Eject"),
        Binding("question_mark", "help", "Help"),
        Binding("1", "movie_full", "Movie+Extras", show=False),
        Binding("2", "movie_main", "Main Only", show=False),
        Binding("3", "multi_disc", "Multi-Disc", show=False),
        Binding("4", "tv_episodes", "TV Episodes", show=False),
        Binding("5", "select_titles", "Select", show=False),
        Binding("6", "disc_info", "Disc Info", show=False),
    ]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.disc_info: DiscInfo | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="main-container"):
            yield Static(
                "Scanning disc...",
                id="disc-status",
            )
            with Vertical(id="menu"):
                yield Label("[bold]Movies[/]", classes="menu-header")
                yield Button(
                    "[1] Rip Movie with Extras",
                    id="btn-movie-full",
                    variant="primary",
                )
                yield Button(
                    "[2] Rip Main Feature Only",
                    id="btn-movie-main",
                )
                yield Button(
                    "[3] Rip Multi-Disc Movie",
                    id="btn-multi-disc",
                )
                yield Label(
                    "[bold]TV Shows[/]", classes="menu-header"
                )
                yield Button(
                    "[4] Rip TV Episodes", id="btn-tv"
                )
                yield Label("[bold]Other[/]", classes="menu-header")
                yield Button(
                    "[5] Select Specific Titles",
                    id="btn-select",
                )
                yield Button(
                    "[6] View Disc Info", id="btn-info"
                )
        yield Footer()

    def on_mount(self) -> None:
        self._update_buttons(enabled=False)
        self._do_scan()

    def action_scan(self) -> None:
        self._do_scan()

    def action_eject(self) -> None:
        from ripper.utils.drive import eject_disc

        eject_disc(self.settings.device)
        self.notify("Disc ejected")

    def action_help(self) -> None:
        self.notify(
            "[S]can  [E]ject  [Q]uit\n"
            "[1] Movie+Extras  [2] Main Only  [3] Multi-Disc\n"
            "[4] TV Episodes  [5] Select Titles  [6] Disc Info",
            title="Keyboard Shortcuts",
        )

    def action_movie_full(self) -> None:
        self._prompt_movie_name("full")

    def action_movie_main(self) -> None:
        self._prompt_movie_name("main")

    def action_multi_disc(self) -> None:
        self._prompt_movie_name("multi")

    def action_tv_episodes(self) -> None:
        self._prompt_tv_info()

    def action_select_titles(self) -> None:
        self._show_title_selection()

    def action_disc_info(self) -> None:
        self._show_disc_info()

    @work(thread=True)
    def _do_scan(self) -> None:
        """Scan disc in background thread."""
        status = self.query_one("#disc-status", Static)
        self.app.call_from_thread(status.update, "Scanning disc...")

        try:
            self.disc_info = scan_disc(self.settings)
            classify_titles(
                self.disc_info.titles,
                self.settings.min_main_length,
            )
            self.disc_info.detected_media_type = detect_media_type(
                self.disc_info.titles,
                self.settings.min_main_length,
            )

            cleaned = clean_disc_name(self.disc_info.name)
            media_label = {
                MediaType.MOVIE: "Movie",
                MediaType.TV_SHOW: "TV Show",
                MediaType.UNKNOWN: "Unknown",
            }[self.disc_info.detected_media_type]

            main_count = len(self.disc_info.main_titles)
            extra_count = len(self.disc_info.extra_titles)
            total = len(self.disc_info.titles)

            status_text = (
                f"[dim]{self.disc_info.name}[/]\n"
                f"[bold]{cleaned}[/] — {media_label}\n"
                f"[dim]{total} titles "
                f"({main_count} main, {extra_count} extras)[/]"
            )
            self.app.call_from_thread(status.update, status_text)
            self.app.call_from_thread(
                self._update_buttons, enabled=True
            )

            # Auto-lookup TMDb if configured
            if (
                self.settings.auto_lookup
                and self.settings.tmdb_api_key
            ):
                self._do_tmdb_lookup(cleaned)

        except Exception as e:
            self.app.call_from_thread(
                status.update, f"[red]{e}[/]"
            )
            logger.error("Scan failed: %s", e)

    def _do_tmdb_lookup(self, cleaned_name: str) -> None:
        """Look up title on TMDb and update disc info."""
        from ripper.metadata.matcher import match_title
        from ripper.metadata.tmdb import TMDbClient

        async def _lookup():
            client = TMDbClient(self.settings.tmdb_api_key)
            try:
                results = await client.search_movie(cleaned_name)
                match = match_title(
                    cleaned_name,
                    results,
                    threshold=self.settings.fuzzy_threshold,
                )
                if match and self.disc_info:
                    self.disc_info.tmdb_id = match.get("id")
                    title = match.get("title", "")
                    year = match.get(
                        "release_date", ""
                    )[:4]
                    self.disc_info.tmdb_title = title
                    if year:
                        self.disc_info.year = int(year)

                    status = self.query_one(
                        "#disc-status", Static
                    )
                    display = f"{title} ({year})" if year else title
                    main_count = len(self.disc_info.main_titles)
                    extra_count = len(self.disc_info.extra_titles)
                    total = len(self.disc_info.titles)

                    self.app.call_from_thread(
                        status.update,
                        f"[dim]{self.disc_info.name}[/]\n"
                        f"[bold green]{display}[/]\n"
                        f"[dim]{total} titles "
                        f"({main_count} main, "
                        f"{extra_count} extras)[/]",
                    )
            finally:
                await client.close()

        asyncio.run(_lookup())

    def _update_buttons(self, enabled: bool) -> None:
        for button in self.query(Button):
            button.disabled = not enabled

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.disc_info is None:
            self.notify("Scan a disc first", severity="warning")
            return

        match event.button.id:
            case "btn-movie-full":
                self._prompt_movie_name("full")
            case "btn-movie-main":
                self._prompt_movie_name("main")
            case "btn-multi-disc":
                self._prompt_movie_name("multi")
            case "btn-tv":
                self._prompt_tv_info()
            case "btn-select":
                self._show_title_selection()
            case "btn-info":
                self._show_disc_info()

    def _prompt_movie_name(self, mode: str) -> None:
        if self.disc_info is None:
            self.notify("Scan a disc first", severity="warning")
            return
        suggested = self._suggested_name()
        self.app.push_screen(
            MovieNameScreen(
                self.settings, self.disc_info, suggested, mode
            )
        )

    def _prompt_tv_info(self) -> None:
        if self.disc_info is None:
            self.notify("Scan a disc first", severity="warning")
            return
        self.app.push_screen(
            TVInfoScreen(self.settings, self.disc_info)
        )

    def _show_title_selection(self) -> None:
        if self.disc_info is None:
            self.notify("Scan a disc first", severity="warning")
            return
        self.app.push_screen(
            ScanScreen(self.settings, self.disc_info)
        )

    def _show_disc_info(self) -> None:
        if self.disc_info is None:
            self.notify("Scan a disc first", severity="warning")
            return
        self.app.push_screen(DiscInfoScreen(self.disc_info))

    def _suggested_name(self) -> str:
        """Best movie name suggestion from TMDb or disc name."""
        if self.disc_info is None:
            return ""
        if self.disc_info.tmdb_title and self.disc_info.year:
            return (
                f"{self.disc_info.tmdb_title} "
                f"({self.disc_info.year})"
            )
        if self.disc_info.tmdb_title:
            return self.disc_info.tmdb_title
        return clean_disc_name(self.disc_info.name)


class MovieNameScreen(Screen):
    """Prompt for movie name before ripping."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        settings: Settings,
        disc_info: DiscInfo,
        suggested_name: str,
        mode: str,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.disc_info = disc_info
        self.suggested_name = suggested_name
        self.mode = mode

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="prompt-container"):
            yield Label(
                "Enter movie name (e.g. 'Dune (2021)'):"
            )
            yield Input(
                value=self.suggested_name,
                id="movie-name-input",
            )
            if self.mode == "multi":
                yield Label("Number of discs:")
                yield Input(value="2", id="disc-count-input")
            with Horizontal(classes="button-row"):
                yield Button(
                    "Rip",
                    variant="primary",
                    id="btn-confirm",
                )
                yield Button("Cancel", id="btn-cancel")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input fields."""
        self._do_confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
            return
        self._do_confirm()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def _do_confirm(self) -> None:
        name = self.query_one(
            "#movie-name-input", Input
        ).value.strip()
        if not name:
            self.notify("Name cannot be empty", severity="warning")
            return

        disc_count = 1
        if self.mode == "multi":
            try:
                disc_count = int(
                    self.query_one(
                        "#disc-count-input", Input
                    ).value
                )
            except ValueError:
                self.notify(
                    "Invalid disc count", severity="error"
                )
                return

        self.app.pop_screen()
        self.app.push_screen(
            ConfirmRipScreen(
                self.settings,
                self.disc_info,
                name,
                self.mode,
                disc_count,
            )
        )


class TVInfoScreen(Screen):
    """Prompt for TV show name and season before ripping."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, settings: Settings, disc_info: DiscInfo
    ) -> None:
        super().__init__()
        self.settings = settings
        self.disc_info = disc_info

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="prompt-container"):
            yield Label("Show name (e.g. 'Seinfeld'):")
            yield Input(id="show-name-input")
            yield Label("Season number:")
            yield Input(value="1", id="season-input")
            with Horizontal(classes="button-row"):
                yield Button(
                    "Rip",
                    variant="primary",
                    id="btn-confirm",
                )
                yield Button("Cancel", id="btn-cancel")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._do_confirm()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
            return
        self._do_confirm()

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def _do_confirm(self) -> None:
        show = self.query_one(
            "#show-name-input", Input
        ).value.strip()
        if not show:
            self.notify(
                "Show name cannot be empty", severity="warning"
            )
            return

        try:
            season = int(
                self.query_one("#season-input", Input).value
            )
        except ValueError:
            self.notify(
                "Invalid season number", severity="error"
            )
            return

        self.app.pop_screen()
        self.app.push_screen(
            ConfirmRipScreen(
                self.settings,
                self.disc_info,
                show,
                "tv",
                season_num=season,
            )
        )


class ConfirmRipScreen(Screen):
    """Confirmation screen showing what will be ripped."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        settings: Settings,
        disc_info: DiscInfo,
        name: str,
        mode: str,
        disc_count: int = 1,
        season_num: int = 1,
        selected_title_ids: set[int] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.disc_info = disc_info
        self.rip_name = name
        self.mode = mode
        self.disc_count = disc_count
        self.season_num = season_num
        self.selected_title_ids = selected_title_ids

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="prompt-container"):
            yield Static(self._build_summary(), id="rip-summary")
            with Horizontal(classes="button-row"):
                yield Button(
                    "Start Rip",
                    variant="primary",
                    id="btn-start",
                )
                yield Button("Cancel", id="btn-cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#btn-start", Button).focus()

    def _build_summary(self) -> str:
        """Build a human-readable summary of the rip."""
        lines = [f"[bold]Ready to rip: {self.rip_name}[/]\n"]

        mode_labels = {
            "full": "Movie with all extras",
            "main": "Main feature only",
            "multi": f"Multi-disc movie ({self.disc_count} discs)",
            "tv": f"TV Season {self.season_num}",
            "select": "Selected titles",
        }
        lines.append(f"Mode: {mode_labels.get(self.mode, self.mode)}")

        titles = self._get_titles()
        total_size = sum(t.size_bytes for t in titles)
        total_dur = sum(t.duration_seconds for t in titles)

        from ripper.utils.formatting import fmt_duration, fmt_size

        lines.append(f"Titles: {len(titles)}")
        lines.append(f"Total size: ~{fmt_size(total_size)}")
        lines.append(f"Total duration: {fmt_duration(total_dur)}")
        lines.append("")

        for t in titles:
            marker = "*" if t.is_main_feature else " "
            name = t.name[:30]
            lines.append(
                f" {marker} {t.id:>2d}  {name:<30s} "
                f"{t.duration_display:>11s}  {t.size_display:>8s}"
            )

        return "\n".join(lines)

    def _get_titles(self):
        if self.mode == "main":
            return self.disc_info.main_titles
        if self.selected_title_ids:
            return [
                t
                for t in self.disc_info.titles
                if t.id in self.selected_title_ids
            ]
        return self.disc_info.titles

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.app.pop_screen()
            return

        self.app.pop_screen()
        self.app.push_screen(
            RipScreen(
                self.settings,
                self.disc_info,
                self.rip_name,
                self.mode,
                self.disc_count,
                season_num=self.season_num,
                selected_title_ids=self.selected_title_ids,
            )
        )

    def action_cancel(self) -> None:
        self.app.pop_screen()
