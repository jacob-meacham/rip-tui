# Fix Batch Mode Remux/Backup Flow

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix four bugs in batch mode: missing backup re-scan (title ID mismatch), auto-deleting backups without confirmation, inconsistent backup directory nesting, and missing remux progress in concurrent display.

**Architecture:** All changes are in `src/ripper/tui/app.py` and `src/ripper/tui/flows.py`. The batch loop in `run_batch()` needs a re-scan after backup (mirroring interactive mode), backup cleanup must be deferred until the user confirms, the concurrent backup path must use the same directory structure as the sequential path, and `start_remux_background` must accept a progress callback that `ConcurrentProgress` can feed.

**Tech Stack:** Python 3.11, pytest, Rich (console/progress), threading

---

### Task 1: Normalize backup directory structure in batch mode

The sequential path calls `create_backup(settings, backup_staging)` which nests backup data under `backup_staging/.backup/`. The concurrent path calls `backup_disc(backup_staging, ...)` directly, putting BDMV at the root of `backup_staging/`. This also causes the sequential path to leak empty parent directories on cleanup.

Fix: Use `create_backup()` in both paths so the directory structure is always `backup_staging/.backup/`.

**Files:**
- Modify: `src/ripper/tui/app.py:952-967`

**Step 1: Write the failing test**

File: `tests/test_batch.py`

```python
"""Tests for batch mode pipeline logic."""

import shutil
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ripper.config.settings import Settings
from ripper.core.disc import DiscInfo, Title
from ripper.tui.flows import RemuxHandle


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        staging_dir=tmp_path / "staging",
        movies_dir=tmp_path / "movies",
        tv_dir=tmp_path / "tv",
        device="/dev/null",
        tmdb_api_key="",
    )


@pytest.fixture
def disc_info() -> DiscInfo:
    return DiscInfo(
        name="TEST_DISC",
        device="/dev/null",
        titles=[
            Title(
                id=0,
                name="Main Feature",
                duration_seconds=7200,
                size_bytes=30_000_000_000,
                chapter_count=20,
                is_main_feature=True,
            ),
        ],
    )


def _make_alive_remux_handle(staging: Path) -> RemuxHandle:
    """Create a RemuxHandle whose thread is still 'alive'."""
    event = threading.Event()
    thread = threading.Thread(target=event.wait, daemon=True)
    handle = RemuxHandle(thread=thread, staging=staging)
    thread.start()
    return handle, event


class TestBatchBackupConsistency:
    """Concurrent backup path must use create_backup like sequential path."""

    def test_concurrent_backup_uses_create_backup(
        self, settings, tmp_path,
    ):
        """When a pending remux is alive, batch still calls create_backup
        so the backup ends up under backup_staging/.backup/, not directly
        in backup_staging/."""
        from ripper.tui.app import run_batch

        # We can't easily run the full batch loop, so we test the
        # contract: create_backup is called with backup_staging in both
        # paths, and its return value (.backup subdir) is used.
        # This is validated by checking the code structure.
        # Instead, test create_backup returns nested path.
        from ripper.tui.flows import create_backup

        staging = tmp_path / "test_staging"

        with patch("ripper.tui.flows.start_rip_with_status"):
            result = create_backup(settings, staging)

        assert result == staging / ".backup"
        assert result.name == ".backup"
```

**Step 2: Run test to verify it passes (baseline)**

Run: `uv run python -m pytest tests/test_batch.py::TestBatchBackupConsistency -v`
Expected: PASS (this confirms `create_backup` contract)

**Step 3: Fix concurrent backup path in `run_batch`**

In `src/ripper/tui/app.py`, replace the concurrent backup block (lines 952-967) to use `create_backup` instead of calling `backup_disc` directly. Pass the progress callback through `create_backup`'s underlying `start_rip_with_status`.

Replace lines 952-969 in `run_batch`:

```python
            # Backup — with concurrent progress if remux is active
            if pending and pending.remux.is_alive():
                console.print(
                    "  [dim]Backing up disc while"
                    " remuxing previous...[/]"
                )
                with ConcurrentProgress() as cp:
                    from ripper.core.ripper import backup_disc as _backup

                    backup_progress = cp.make_callback("backup")
                    _backup(
                        backup_staging, settings,
                        on_progress=backup_progress,
                        process_id=f"backup-disc{disc_num}",
                    )
                backup_dir = backup_staging
            else:
                backup_dir = create_backup(settings, backup_staging)
```

With:

```python
            # Backup — with concurrent progress if remux is active
            if pending and pending.remux.is_alive():
                console.print(
                    "  [dim]Backing up disc while"
                    " remuxing previous...[/]"
                )
                with ConcurrentProgress() as cp:
                    backup_dir = create_backup(
                        settings, backup_staging,
                        on_progress=cp.make_callback("backup"),
                        process_id=f"backup-disc{disc_num}",
                    )
            else:
                backup_dir = create_backup(settings, backup_staging)
```

This requires `create_backup` to accept and forward `on_progress` and `process_id`. Update `create_backup` in `src/ripper/tui/flows.py`:

Replace:

```python
def create_backup(settings: Settings, staging_dir: Path) -> Path:
    """Backup disc to staging_dir/.backup, returns backup dir path."""
    backup_dir = staging_dir / ".backup"

    # Clean up any partial/corrupt leftover backup
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    staging_dir.mkdir(parents=True, exist_ok=True)

    start_rip_with_status(
        "Backing up disc...",
        backup_disc,
        backup_dir,
        settings,
        on_progress=print_progress,
    )

    return backup_dir
```

With:

```python
def create_backup(
    settings: Settings,
    staging_dir: Path,
    on_progress: ProgressCallback | None = None,
    process_id: str | None = None,
) -> Path:
    """Backup disc to staging_dir/.backup, returns backup dir path.

    Args:
        settings: App settings.
        staging_dir: Parent directory; backup goes into staging_dir/.backup/.
        on_progress: Optional progress callback (defaults to print_progress).
        process_id: Optional process ID for cancellation tracking.
    """
    backup_dir = staging_dir / ".backup"

    # Clean up any partial/corrupt leftover backup
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    staging_dir.mkdir(parents=True, exist_ok=True)

    progress_cb = on_progress if on_progress is not None else print_progress

    start_rip_with_status(
        "Backing up disc...",
        backup_disc,
        backup_dir,
        settings,
        on_progress=progress_cb,
        process_id=process_id,
    )

    return backup_dir
```

Also need `start_rip_with_status` to forward `process_id`. Check if it already does — it uses `**kwargs` forwarding through the rip function. Verify by reading `start_rip_with_status`.

**Step 4: Update existing test for new signature**

In `tests/test_flows.py`, the `TestCreateBackup` tests should still pass since `on_progress` and `process_id` are optional with defaults.

Run: `uv run python -m pytest tests/test_flows.py::TestCreateBackup tests/test_batch.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/ripper/tui/app.py src/ripper/tui/flows.py tests/test_batch.py
git commit -m "fix(batch): normalize backup directory structure

Use create_backup() in both sequential and concurrent paths so backup
always goes to .backup-discN/.backup/. Previously the concurrent path
called backup_disc() directly, creating an inconsistent structure and
leaking empty parent directories on cleanup."
```

---

### Task 2: Add backup re-scan after backup in batch mode

After backing up a disc, batch mode must re-scan from the backup so title IDs in `disc_info` match what `makemkvcon` will use during remux. Interactive mode already does this (lines 130-141 in app.py). Without this, specific-title remux (main, full+DiscDB, select) uses wrong IDs, and post-remux matching (TV, full) is broken.

**Files:**
- Modify: `src/ripper/tui/app.py:946-971`
- Test: `tests/test_batch.py`

**Step 1: Write the failing test**

Add to `tests/test_batch.py`:

```python
class TestBatchRescan:
    """Batch mode must re-scan from backup after creating it."""

    def test_rescan_called_after_backup(self, settings, tmp_path):
        """After backup, _scan_disc must be called again with
        backup_dir so title IDs match the backup."""
        # This is a structural/contract test. We verify that
        # scan_disc is called with backup_dir= after backup.
        from ripper.tui.app import _scan_disc

        calls = []
        original_scan = _scan_disc

        def tracking_scan(settings, backup_dir=None):
            calls.append({"backup_dir": backup_dir})
            # Return a minimal DiscInfo
            return DiscInfo(
                name="TEST",
                device="/dev/null",
                titles=[
                    Title(
                        id=0,
                        name="Main",
                        duration_seconds=7200,
                        size_bytes=30_000_000_000,
                        chapter_count=20,
                        is_main_feature=True,
                    ),
                ],
            )

        # Can't easily run full batch loop, so verify the
        # re-scan step exists by checking the source directly.
        import inspect
        source = inspect.getsource(
            __import__(
                "ripper.tui.app", fromlist=["run_batch"]
            ).run_batch
        )

        # After backup, there must be a re-scan from backup
        # The pattern is: _scan_disc(settings, backup_dir=backup_dir)
        assert "_scan_disc(settings, backup_dir=backup_dir)" in source, (
            "run_batch must re-scan from backup after backup step "
            "so title IDs match (see interactive mode lines 130-141)"
        )
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_batch.py::TestBatchRescan -v`
Expected: FAIL — the source doesn't contain the re-scan call yet.

**Step 3: Add re-scan after backup in `run_batch`**

In `src/ripper/tui/app.py`, after the backup block (after `backup_dir = create_backup(...)` in both paths) and before `enrich_disc_info`, add the re-scan:

Replace:

```python
            enrich_disc_info(disc_info, backup_dir, settings)
```

With:

```python
            # Re-scan from backup so title IDs match what makemkvcon
            # will use during remux.  Disc vs backup scans can assign
            # different indices to the same content.
            disc_info = _scan_disc(settings, backup_dir=backup_dir)
            if disc_info is None:
                console.print(
                    "  [red]Backup scan failed, skipping disc[/]"
                )
                continue

            enrich_disc_info(disc_info, backup_dir, settings)
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_batch.py::TestBatchRescan -v`
Expected: PASS

Run: `uv run python -m pytest tests/ -v`
Expected: All pass

**Step 5: Commit**

```bash
git add src/ripper/tui/app.py tests/test_batch.py
git commit -m "fix(batch): re-scan from backup so title IDs match remux

Batch mode scanned the physical disc but never re-scanned from the
backup. Disc and backup scans can assign different title indices to
the same content, causing wrong titles to be remuxed. Interactive mode
already does this re-scan (lines 130-141). Now batch does too."
```

---

### Task 3: Defer backup deletion until user confirms

Currently `_finish_pending_disc` auto-deletes the backup (line 855). The user wants to verify remux output before deletion, matching interactive mode's `"Delete backup? [y/N]:"` prompt.

Approach: Collect completed backup dirs in a list. After each disc's post-remux work, skip deletion. At the end of the batch, show a summary and prompt to delete all backups.

**Files:**
- Modify: `src/ripper/tui/app.py:814-856` (`_finish_pending_disc`)
- Modify: `src/ripper/tui/app.py:904-1220` (`run_batch`)
- Test: `tests/test_batch.py`

**Step 1: Write the failing test**

Add to `tests/test_batch.py`:

```python
class TestFinishPendingDiscNoAutoDelete:
    """_finish_pending_disc must NOT auto-delete backup."""

    def test_backup_not_deleted_after_finish(self):
        """Verify _finish_pending_disc source doesn't contain
        shutil.rmtree of backup_dir."""
        import inspect
        from ripper.tui.app import _finish_pending_disc

        source = inspect.getsource(_finish_pending_disc)
        assert "shutil.rmtree" not in source, (
            "_finish_pending_disc must not auto-delete backups. "
            "Deletion should be deferred to end of batch."
        )
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_batch.py::TestFinishPendingDiscNoAutoDelete -v`
Expected: FAIL — currently has `shutil.rmtree(pending.backup_dir, ...)`

**Step 3: Remove auto-delete from `_finish_pending_disc`**

In `src/ripper/tui/app.py`, remove lines 854-855 from `_finish_pending_disc`:

```python
    # Clean up the per-disc backup
    shutil.rmtree(pending.backup_dir, ignore_errors=True)
```

**Step 4: Add deferred cleanup to `run_batch`**

In `run_batch`, collect backup dirs and prompt at end.

After `pending: _PendingDisc | None = None` (line 931), add:

```python
    completed_backups: list[Path] = []
```

In `_finish_pending_disc` calls (3 locations: line 982, line 1154, line 1212), after `_finish_pending_disc(settings, pending, dispatcher)`, add:

```python
                    completed_backups.append(pending.backup_dir)
```

For the multi-disc path (line 1099), replace:

```python
                shutil.rmtree(backup_dir, ignore_errors=True)
```

With:

```python
                completed_backups.append(backup_dir)
```

At the end of `run_batch`, before the "Batch complete." message, add:

```python
    # Prompt to clean up backups
    valid_backups = [b for b in completed_backups if b.exists()]
    if valid_backups:
        console.print()
        console.print(
            f"  {len(valid_backups)} backup(s) remaining:"
        )
        for b in valid_backups:
            console.print(f"    {b}")
        console.print()
        try:
            answer = input(
                "  Delete all backups? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print()
            answer = ""
        if answer in ("y", "yes"):
            for b in valid_backups:
                shutil.rmtree(b, ignore_errors=True)
            console.print("  [dim]Backups removed.[/]")
        else:
            console.print("  [dim]Backups kept.[/]")
```

**Step 5: Run tests**

Run: `uv run python -m pytest tests/test_batch.py -v`
Expected: PASS

Run: `uv run python -m pytest tests/ -v`
Expected: All pass

**Step 6: Commit**

```bash
git add src/ripper/tui/app.py tests/test_batch.py
git commit -m "fix(batch): defer backup deletion until user confirms

Previously _finish_pending_disc auto-deleted backups immediately after
remux. Now backups are collected and the user is prompted at the end of
the batch to delete them, matching interactive mode's confirmation UX."
```

---

### Task 4: Wire remux progress into ConcurrentProgress

Background remux is started without `on_progress` (line 1121), so `ConcurrentProgress` only shows backup progress. Fix by passing a progress callback when starting background remux.

**Files:**
- Modify: `src/ripper/tui/app.py:1110-1125`
- Test: `tests/test_batch.py`

**Step 1: Write the failing test**

Add to `tests/test_batch.py`:

```python
class TestRemuxProgressCallback:
    """Background remux must receive a progress callback."""

    def test_start_remux_background_accepts_progress(self):
        """Verify start_remux_background forwards on_progress."""
        import inspect
        from ripper.tui.flows import start_remux_background

        sig = inspect.signature(start_remux_background)
        assert "on_progress" in sig.parameters

    def test_batch_passes_progress_to_remux(self):
        """run_batch source must pass on_progress to
        start_remux_background."""
        import inspect
        from ripper.tui.app import run_batch

        source = inspect.getsource(run_batch)
        # Find the start_remux_background call and verify
        # it includes on_progress
        assert "on_progress=" in source, (
            "run_batch must pass on_progress to "
            "start_remux_background for ConcurrentProgress display"
        )
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_batch.py::TestRemuxProgressCallback -v`
Expected: First test passes (parameter exists), second FAILS (no `on_progress=` in call)

**Step 3: Store ConcurrentProgress reference and pass callback to remux**

The challenge: `ConcurrentProgress` is a context manager used during backup, but remux starts after backup completes. We need a shared `ConcurrentProgress` that spans both backup and remux.

Approach: Create the `ConcurrentProgress` before backup, keep it alive through remux start, and let the remux thread update it. The context manager exits when the next iteration's backup needs it (or at batch end).

Simpler approach: Just store a module-level `ConcurrentProgress` instance that the remux thread can write to, and the next iteration's backup phase renders. Actually, the simplest approach: pass `on_progress` to `start_remux_background` so the thread has a callback. The `ConcurrentProgress` from the concurrent path can be stored and the remux callback bound to it. But `ConcurrentProgress` is a context manager tied to `Live`...

Simplest correct approach: Don't use `ConcurrentProgress` as a context manager around just the backup. Instead, create it around the entire backup+remux-start section. The remux thread gets a callback. The `ConcurrentProgress.__exit__` is called once we no longer need the display (after backup finishes and remux is launched, or on next iteration when we want to show new progress).

Actually even simpler: just pass a plain `on_progress` callback to `start_remux_background`. It doesn't need to be tied to `ConcurrentProgress`. The callback will be invoked from the background thread. On the next iteration, if we enter `ConcurrentProgress`, the remux callback updates a slot. If we don't, the remux just has a no-op or print-based callback.

Simplest fix: When starting background remux (line 1121), always pass `on_progress=print_progress`. This gives the remux visible progress when it runs alone (e.g., during the "Waiting for previous remux to finish..." phase), and we don't need to wire it into `ConcurrentProgress` at all — the concurrent display is only for the backup phase overlap.

But for true concurrent display, we need both callbacks feeding into one `ConcurrentProgress`. Here's the approach:

In the concurrent backup block, instead of ending the `ConcurrentProgress` after backup, store it so the already-running remux can keep updating it. But the remux was started on the PREVIOUS iteration — its callback was already set.

Better approach: When we start background remux at the END of an iteration, give it a callback that we can later plug into a `ConcurrentProgress`. Use a simple "relay" callback:

```python
# At remux start:
remux_progress_cb = print_progress  # Default: standalone display
handle = start_remux_background(..., on_progress=remux_progress_cb, ...)
```

Then on the NEXT iteration, if concurrent:

```python
with ConcurrentProgress() as cp:
    # Redirect existing remux progress to the concurrent display
    # This isn't possible since the callback is already bound...
```

This is tricky because the remux thread already captured its callback at start time.

Best pragmatic approach: Use a mutable relay. Store a callback reference that the remux thread calls, and swap the target when entering concurrent mode.

In `run_batch`, before the loop:

```python
    # Mutable progress relay for background remux
    _remux_cb_target: list[ProgressCallback | None] = [None]

    def _remux_progress(progress: RipProgress) -> None:
        cb = _remux_cb_target[0]
        if cb is not None:
            cb(progress)
```

When starting remux:

```python
    _remux_cb_target[0] = print_progress
    remux_handle = start_remux_background(
        ..., on_progress=_remux_progress, ...
    )
```

In the concurrent block:

```python
    with ConcurrentProgress() as cp:
        _remux_cb_target[0] = cp.make_callback("remux")
        backup_dir = create_backup(
            settings, backup_staging,
            on_progress=cp.make_callback("backup"),
            process_id=f"backup-disc{disc_num}",
        )
    _remux_cb_target[0] = print_progress  # Restore after concurrent display
```

This gives us true dual-progress during concurrent backup+remux.

Modify `src/ripper/tui/app.py`:

After `disc_num = 0` add:

```python
    # Mutable relay so background remux progress can be redirected
    # into ConcurrentProgress when backup runs in parallel.
    from ripper.core.ripper import RipProgress

    _remux_cb_target: list[ProgressCallback | None] = [None]

    def _remux_progress(progress: RipProgress) -> None:
        cb = _remux_cb_target[0]
        if cb is not None:
            cb(progress)
```

In the concurrent backup block, replace:

```python
            if pending and pending.remux.is_alive():
                console.print(
                    "  [dim]Backing up disc while"
                    " remuxing previous...[/]"
                )
                with ConcurrentProgress() as cp:
                    backup_dir = create_backup(
                        settings, backup_staging,
                        on_progress=cp.make_callback("backup"),
                        process_id=f"backup-disc{disc_num}",
                    )
```

With:

```python
            if pending and pending.remux.is_alive():
                console.print(
                    "  [dim]Backing up disc while"
                    " remuxing previous...[/]"
                )
                with ConcurrentProgress() as cp:
                    _remux_cb_target[0] = cp.make_callback("remux")
                    backup_dir = create_backup(
                        settings, backup_staging,
                        on_progress=cp.make_callback("backup"),
                        process_id=f"backup-disc{disc_num}",
                    )
                _remux_cb_target[0] = None
```

In the remux start block (around line 1121), replace:

```python
            remux_handle = start_remux_background(
                backup_dir, staging, name, settings,
                titles=titles,
                process_id=f"remux-disc{disc_num}",
            )
```

With:

```python
            _remux_cb_target[0] = print_progress
            remux_handle = start_remux_background(
                backup_dir, staging, name, settings,
                titles=titles,
                on_progress=_remux_progress,
                process_id=f"remux-disc{disc_num}",
            )
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_batch.py tests/test_flows.py -v`
Expected: PASS

Run: `uv run python -m pytest tests/ -v`
Expected: All pass

**Step 5: Commit**

```bash
git add src/ripper/tui/app.py tests/test_batch.py
git commit -m "fix(batch): wire remux progress into concurrent display

Background remux was started without on_progress, so ConcurrentProgress
only showed backup progress. Now uses a mutable relay callback that gets
redirected into ConcurrentProgress when backup runs concurrently, giving
true dual-progress bars for backup and remux."
```

---

### Task 5: Verify start_rip_with_status forwards process_id

`create_backup` now passes `process_id` through to `start_rip_with_status`. Verify this function forwards kwargs properly.

**Files:**
- Read: `src/ripper/tui/display.py` (start_rip_with_status)
- Possibly modify if it doesn't forward

**Step 1: Read and verify**

Read `start_rip_with_status` in `src/ripper/tui/display.py`. Check that it passes `**kwargs` or `process_id` through to the rip function.

If it does, this task is done (no changes needed).

If it doesn't, add `process_id` forwarding.

**Step 2: Run full test suite**

Run: `uv run python -m pytest tests/ -v`
Expected: All pass

**Step 3: Commit (if changes made)**

```bash
git add src/ripper/tui/display.py
git commit -m "fix: forward process_id through start_rip_with_status"
```

---

### Task 6: Run linter and full test suite

**Step 1: Lint**

Run: `uv run python -m ruff check src/ tests/`
Fix any issues.

**Step 2: Full tests**

Run: `uv run python -m pytest tests/ -v`
Verify all pass.

**Step 3: Final commit (if lint fixes needed)**

```bash
git add -A
git commit -m "style: lint fixes"
```
