"""Ripping progress screen."""

import logging
import threading
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Label,
    ProgressBar,
    Static,
)

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, ExtraType
from ripper.core.organizer import (
    organize_movie,
    organize_multi_disc,
    organize_tv,
)
from ripper.core.ripper import (
    RipCancelledError,
    RipProgress,
    cancel_active_rip,
    rip_all_titles,
    rip_titles,
)
from ripper.utils.drive import eject_disc, wait_for_disc
from ripper.utils.formatting import fmt_duration, fmt_size

logger = logging.getLogger(__name__)


class RipScreen(Screen):
    """Shows ripping progress with live updates."""

    BINDINGS = [Binding("c", "cancel", "Cancel")]

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
        self._cancelled = False
        self._completed_titles: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="rip-container"):
            yield Label(
                f"[bold]Ripping: {self.rip_name}[/]",
                id="rip-title",
            )
            yield Static("Preparing...", id="current-title")
            yield ProgressBar(id="rip-progress", total=100)
            yield Static("", id="rip-eta")
            yield Static("", id="completed-list")
            yield Static("", id="rip-status-extra")
        yield Footer()

    def on_mount(self) -> None:
        self._start_rip()

    @work(thread=True)
    def _start_rip(self) -> None:
        """Run the rip operation in a background thread."""
        try:
            match self.mode:
                case "full":
                    self._rip_movie_full()
                case "main":
                    self._rip_movie_main()
                case "multi":
                    self._rip_multi_disc()
                case "tv":
                    self._rip_tv()
                case "select":
                    self._rip_selected()

            self.app.call_from_thread(self._on_rip_complete)
        except RipCancelledError:
            self.app.call_from_thread(
                self._on_rip_error, "Cancelled by user"
            )
        except Exception as e:
            logger.error("Rip failed: %s", e)
            self.app.call_from_thread(self._on_rip_error, str(e))

    def _rip_movie_full(self) -> None:
        staging = self.settings.staging_dir / self.rip_name
        rip_all_titles(
            staging,
            self.settings,
            on_progress=self._update_progress,
        )

        # Check for extras to classify
        mkvs = sorted(
            staging.glob("*.mkv"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        extras = mkvs[1:]  # Everything after largest

        if extras:
            # Show OrganizeScreen and wait for user
            self._extras_event = threading.Event()
            self._extras_map: dict[Path, ExtraType] = {}
            self.app.call_from_thread(
                self._show_organize_screen, extras
            )
            self._extras_event.wait()
            extras_map = self._extras_map
        else:
            extras_map = None

        self._update_status("Organizing files...")
        organize_movie(
            staging, self.rip_name, self.settings,
            extras_map=extras_map,
        )
        if self.settings.auto_eject:
            eject_disc(self.settings.device)

    def _show_organize_screen(
        self, extras: list[Path]
    ) -> None:
        """Push OrganizeScreen from the main thread."""
        from ripper.tui.screens.organize import OrganizeScreen

        def on_classify(
            classifications: dict[Path, ExtraType],
        ) -> None:
            self._extras_map = classifications
            self._extras_event.set()

        self.app.push_screen(
            OrganizeScreen(extras, on_complete=on_classify)
        )

    def _rip_movie_main(self) -> None:
        staging = self.settings.staging_dir / self.rip_name
        main_titles = self.disc_info.main_titles
        if not main_titles:
            raise RuntimeError("No main feature detected")
        rip_titles(
            main_titles,
            staging,
            self.settings,
            on_progress=self._update_progress,
        )
        self._update_status("Organizing files...")
        organize_movie(staging, self.rip_name, self.settings)
        if self.settings.auto_eject:
            eject_disc(self.settings.device)

    def _rip_multi_disc(self) -> None:
        disc_dirs: list[Path] = []

        for d in range(1, self.disc_count + 1):
            if d > 1:
                self._update_status(
                    f"[bold yellow]Insert disc {d} "
                    f"and close the tray...[/]\n"
                    f"Waiting for disc..."
                )
                if self.settings.auto_eject:
                    eject_disc(self.settings.device)
                if not wait_for_disc(
                    self.settings.device, timeout_seconds=120
                ):
                    raise RuntimeError(
                        f"Timed out waiting for disc {d}"
                    )

            disc_staging = (
                self.settings.staging_dir
                / f"{self.rip_name}-disc{d}"
            )
            self._update_status(
                f"Ripping disc {d}/{self.disc_count}..."
            )
            rip_all_titles(
                disc_staging,
                self.settings,
                on_progress=self._update_progress,
            )
            disc_dirs.append(disc_staging)

        self._update_status("Organizing and merging files...")
        organize_multi_disc(disc_dirs, self.rip_name, self.settings)
        if self.settings.auto_eject:
            eject_disc(self.settings.device)

    def _rip_tv(self) -> None:
        staging = (
            self.settings.staging_dir
            / f"{self.rip_name}-S{self.season_num:02d}"
        )
        rip_all_titles(
            staging,
            self.settings,
            on_progress=self._update_progress,
        )

        self._update_status("Organizing episodes...")
        mkvs = sorted(
            staging.glob("*.mkv"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )

        episode_map = self._match_tv_episodes(mkvs)
        organize_tv(
            staging,
            self.rip_name,
            self.season_num,
            episode_map,
            self.settings,
        )
        if self.settings.auto_eject:
            eject_disc(self.settings.device)

    def _match_tv_episodes(
        self, mkvs: list[Path]
    ) -> dict[Path, int]:
        """Match MKV files to episode numbers.

        Tries TMDb duration matching first, falls back to
        size-based ordering.
        """
        if self.settings.tmdb_api_key and self.disc_info:
            try:
                episode_map = self._try_tmdb_episode_match(
                    mkvs
                )
                if episode_map:
                    return episode_map
            except Exception:
                logger.warning(
                    "TMDb episode match failed, "
                    "using size-based mapping"
                )

        # Fallback: assign by size descending
        return {mkv: i + 1 for i, mkv in enumerate(mkvs)}

    def _try_tmdb_episode_match(
        self, mkvs: list[Path]
    ) -> dict[Path, int] | None:
        """Try to match episodes using TMDb runtimes."""
        import asyncio

        from ripper.metadata.matcher import (
            match_episodes_by_duration,
            match_title,
        )
        from ripper.metadata.tmdb import TMDbClient

        async def _lookup():
            client = TMDbClient(self.settings.tmdb_api_key)
            try:
                results = await client.search_tv(self.rip_name)
                match = match_title(
                    self.rip_name,
                    results,
                    title_key="name",
                    threshold=self.settings.fuzzy_threshold,
                )
                if not match:
                    return None

                tv_id = match.get("id")
                if not tv_id:
                    return None

                return await client.get_season_episodes(
                    tv_id, self.season_num
                )
            finally:
                await client.close()

        episodes = asyncio.run(_lookup())
        if not episodes:
            return None

        # Build duration lists for matching
        title_durations = self._get_mkv_durations(mkvs)
        episode_runtimes: list[tuple[int, int]] = [
            (ep["episode_number"], ep.get("runtime", 0) * 60)
            for ep in episodes
            if ep.get("runtime")
        ]

        if not episode_runtimes:
            return None

        matches = match_episodes_by_duration(
            title_durations, episode_runtimes
        )
        if not matches:
            return None

        # Convert index-based matches to path-based
        episode_map: dict[Path, int] = {}
        for idx, ep_num in matches.items():
            if idx < len(mkvs):
                episode_map[mkvs[idx]] = ep_num

        # Fill unmatched files with next available numbers
        used_eps = set(episode_map.values())
        next_ep = 1
        for mkv in mkvs:
            if mkv not in episode_map:
                while next_ep in used_eps:
                    next_ep += 1
                episode_map[mkv] = next_ep
                used_eps.add(next_ep)
                next_ep += 1

        return episode_map

    def _get_mkv_durations(
        self, mkvs: list[Path]
    ) -> list[tuple[int, int]]:
        """Get durations for MKV files from disc_info."""
        durations: list[tuple[int, int]] = []
        for i, mkv in enumerate(mkvs):
            dur = 0
            if self.disc_info:
                stem = mkv.stem.lower()
                for title in self.disc_info.titles:
                    patterns = [
                        f"t{title.id:02d}",
                        f"title{title.id:02d}",
                        f"title_{title.id}",
                    ]
                    if any(p in stem for p in patterns):
                        dur = title.duration_seconds
                        break
            durations.append((i, dur))
        return durations

    def _rip_selected(self) -> None:
        staging = self.settings.staging_dir / self.rip_name
        selected = [
            t
            for t in self.disc_info.titles
            if t.id in (self.selected_title_ids or set())
        ]
        rip_titles(
            selected,
            staging,
            self.settings,
            on_progress=self._update_progress,
        )
        self._update_status(f"Files saved to {staging}")

    def _update_progress(self, progress: RipProgress) -> None:
        if self._cancelled:
            return
        self.app.call_from_thread(self._apply_progress, progress)

    def _apply_progress(self, progress: RipProgress) -> None:
        self.query_one("#current-title", Static).update(
            f"Title {progress.title_id}: {progress.title_name}"
        )
        self.query_one(ProgressBar).update(
            progress=progress.percent
        )

        eta_text = f"{progress.percent:.1f}%"
        eta_text += f"  {fmt_size(progress.current_bytes)}"
        eta_text += f" / {fmt_size(progress.total_bytes)}"
        if progress.eta_seconds is not None:
            eta_text += (
                f"  ETA: {fmt_duration(progress.eta_seconds)}"
            )
        self.query_one("#rip-eta", Static).update(eta_text)

    def _update_status(self, text: str) -> None:
        self.app.call_from_thread(
            self.query_one("#rip-status-extra", Static).update,
            text,
        )

    def _on_rip_complete(self) -> None:
        self.query_one("#current-title", Static).update(
            "[green bold]Rip complete![/]"
        )
        self.query_one(ProgressBar).update(progress=100)
        self.query_one("#rip-eta", Static).update("")
        dest = self.settings.movies_dir / self.rip_name
        self.query_one("#rip-status-extra", Static).update(
            f"Output: {dest}"
        )
        self.notify("Rip complete!", title=self.rip_name)

    def _on_rip_error(self, error: str) -> None:
        self.query_one("#current-title", Static).update(
            f"[red bold]Error: {error}[/]"
        )
        self.notify(f"Rip failed: {error}", severity="error")

    def action_cancel(self) -> None:
        self._cancelled = True
        cancel_active_rip()
        self.app.pop_screen()
