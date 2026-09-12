"""Curator — nightly memory lifecycle orchestrator.

Consumes the previously-dead `memory_auto_dream_cron` config (default 23:00)
and runs a four-step nightly maintenance pipeline:

1. Night auto_dream (incremental: pending cards only)
2. Daily governance: log/prioritize expired-but-undistilled daily cards
3. Digest governance: archive cards with low effective score over grace period
4. Full reindex

The curator runs as an asyncio background task in the application lifespan.
It checks every minute whether the configured HH:MM trigger point has been
reached. On startup, if today's trigger point has already passed and the
curator hasn't run yet today, it runs immediately (catch-up after downtime).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from datetime import date, datetime

from app.config import Settings
from app.memory.file_store.markdown_io import read_markdown, write_markdown

logger = logging.getLogger(__name__)


class CuratorJob:
    """Nightly memory curator — asyncio loop with HH:MM trigger."""

    def __init__(
        self,
        settings: Settings,
        memory_service,  # MemoryService
        loop: asyncio.AbstractEventLoop | None = None,
    ):
        self.settings = settings
        self.memory_service = memory_service
        self._loop = loop
        self._task: asyncio.Task | None = None
        self._last_run_date: date | None = None

    async def start(self) -> None:
        """Start the curator background loop."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info("CuratorJob started (cron=%s)", self.settings.memory_auto_dream_cron)

    async def stop(self) -> None:
        """Stop the curator background loop."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("CuratorJob stopped")

    async def _run_loop(self) -> None:
        """Main loop: check every minute if trigger point reached."""
        try:
            # Catch-up: if today's trigger already passed, run immediately
            if self._should_catch_up():
                logger.info("CuratorJob: catch-up run (trigger already passed today)")
                await self._safe_run()
            while True:
                await asyncio.sleep(60)  # check every minute
                if self._should_run_now():
                    await self._safe_run()
        except asyncio.CancelledError:
            logger.info("CuratorJob loop cancelled")
            raise

    def _should_catch_up(self) -> bool:
        """Check if today's trigger point has passed but we haven't run."""
        if self._last_run_date == date.today():
            return False
        try:
            trigger_hour, trigger_minute = self._parse_cron()
            now = datetime.now()
            trigger_today = now.replace(hour=trigger_hour, minute=trigger_minute, second=0, microsecond=0)
            return now >= trigger_today
        except ValueError:
            return False

    def _should_run_now(self) -> bool:
        """Check if we should run: trigger point reached and not yet run today."""
        if self._last_run_date == date.today():
            return False
        try:
            trigger_hour, trigger_minute = self._parse_cron()
            now = datetime.now()
            # Trigger within the first minute of the target window
            return now.hour == trigger_hour and now.minute == trigger_minute
        except ValueError:
            return False

    def _parse_cron(self) -> tuple[int, int]:
        """Parse HH:MM cron string into (hour, minute)."""
        parts = self.settings.memory_auto_dream_cron.strip().split(":")
        if len(parts) != 2:
            raise ValueError(f"Invalid cron format: {self.settings.memory_auto_dream_cron}")
        return int(parts[0]), int(parts[1])

    async def _safe_run(self) -> None:
        """Run the curator pipeline with error handling."""
        try:
            await self.run()
            self._last_run_date = date.today()
        except Exception as e:
            logger.warning("CuratorJob run failed: %s", e)

    async def run(self) -> None:
        """Execute the four-step curator pipeline.

        Each step is independently try/excepted so one failure doesn't block
        subsequent steps.
        """
        logger.info("CuratorJob: starting nightly pipeline")
        start_time = time.time()

        # Step 1: Night auto_dream (incremental: pending cards only)
        try:
            logger.info("CuratorJob step 1: auto_dream")
            result = await self.memory_service.trigger_auto_dream()
            logger.info("CuratorJob step 1 result: %s", result)
            self.memory_service._record_dream_completed()
        except Exception as e:
            logger.warning("CuratorJob step 1 (auto_dream) failed: %s", e)

        # Step 2: Daily governance — log expired undistilled cards
        try:
            logger.info("CuratorJob step 2: daily governance")
            await self._step_daily_governance()
        except Exception as e:
            logger.warning("CuratorJob step 2 (daily governance) failed: %s", e)

        # Step 3: Digest governance — archive low effective-score cards
        try:
            logger.info("CuratorJob step 3: digest governance")
            await self._step_digest_governance()
        except Exception as e:
            logger.warning("CuratorJob step 3 (digest governance) failed: %s", e)

        # Step 4: Full reindex
        try:
            logger.info("CuratorJob step 4: full reindex")
            await asyncio.to_thread(self.memory_service.auto_index.full_reindex)
            logger.info("CuratorJob step 4 complete")
        except Exception as e:
            logger.warning("CuratorJob step 4 (reindex) failed: %s", e)

        elapsed = time.time() - start_time
        logger.info("CuratorJob: nightly pipeline complete in %.1fs", elapsed)

    async def _step_daily_governance(self) -> None:
        """Daily governance: identify expired daily cards that haven't been distilled.

        These cards are past TTL but have no digest inlinks. We log them and
        prioritize triggering dream to distill them.
        """
        ws = self.memory_service.workspace
        ttl_days = self.settings.memory_daily_ttl_days
        now = time.time()
        day_seconds = 86400.0

        expired_undistilled: list[str] = []
        if not ws.daily_dir.exists():
            return

        for f in sorted(ws.daily_dir.rglob("*.md")):
            mem = read_markdown(f)
            if mem is None:
                continue
            # Check age
            created = mem.frontmatter.created_at
            if not created:
                continue
            try:
                d = date.fromisoformat(created.strip())
                ts = time.mktime(d.timetuple())
            except (ValueError, OSError):
                continue
            days_old = (now - ts) / day_seconds
            if days_old <= ttl_days:
                continue
            # Check if distilled (has digest inlinks)
            try:
                rel = str(f.resolve().relative_to(ws.root.resolve()))
            except ValueError:
                rel = str(f)
            has_digest_inlink = False
            if self.memory_service.wikilink_expander:
                for inlink in self.memory_service.wikilink_expander.get_inlinks(rel):
                    if inlink.get("source", "").startswith("digest/"):
                        has_digest_inlink = True
                        break
            if not has_digest_inlink:
                expired_undistilled.append(rel)

        if expired_undistilled:
            logger.info(
                "CuratorJob daily governance: %d expired undistilled cards (TTL=%d days): %s",
                len(expired_undistilled), ttl_days, expired_undistilled[:5],
            )
        else:
            logger.info("CuratorJob daily governance: no expired undistilled cards")

    async def _step_digest_governance(self) -> None:
        """Digest governance: archive cards with sustained low effective score.

        effective = importance × 0.5^(days_since_access / half_life) × log2(2 + access_count)

        A card is archived when effective < memory_archive_score for
        memory_archive_grace_days consecutive days.
        """
        ws = self.memory_service.workspace
        access_stats = self.memory_service.access_stats
        half_life = self.settings.memory_decay_half_life_days
        archive_score = self.settings.memory_archive_score
        grace_days = self.settings.memory_archive_grace_days
        now = time.time()
        day_seconds = 86400.0

        if not ws.digest_dir.exists():
            return

        archived_count = 0
        for f in sorted(ws.digest_dir.rglob("*.md")):
            mem = read_markdown(f)
            if mem is None:
                continue
            if mem.frontmatter.status == "archived":
                continue

            importance = mem.frontmatter.importance

            # Days since access (from access_stats) or fallback to updated_at
            try:
                rel = str(f.resolve().relative_to(ws.root.resolve()))
            except ValueError:
                rel = str(f)

            days_since_access = 0.0
            access_count = 0
            if access_stats:
                stats = access_stats.get(rel)
                if stats:
                    if stats.get("last_accessed", 0) > 0:
                        days_since_access = max(0.0, (now - stats["last_accessed"]) / day_seconds)
                    access_count = int(stats.get("access_count", 0))

            # effective score
            decay = 0.5 ** (days_since_access / half_life) if half_life > 0 else 1.0
            access_factor = math.log2(2 + access_count)
            effective = importance * decay * access_factor

            # Watermark tracking for consecutive days below threshold
            watermark = access_stats.get_watermark(rel) if access_stats else None
            if effective < archive_score:
                if watermark is None:
                    # First day below threshold
                    if access_stats:
                        access_stats.set_watermark(rel, now)
                    logger.debug("CuratorJob: %s first day below threshold (eff=%.4f)", rel, effective)
                else:
                    days_below = (now - watermark) / day_seconds
                    if days_below >= grace_days:
                        # Archive the card
                        mem.frontmatter.status = "archived"
                        mem.frontmatter.updated_at = date.today().isoformat()
                        write_markdown(f, mem.frontmatter, mem.body)
                        # Clear watermark after archiving
                        if access_stats:
                            access_stats.set_watermark(rel, None)
                        archived_count += 1
                        logger.info(
                            "CuratorJob: archived %s (eff=%.4f, %d days below %.2f)",
                            rel, effective, int(days_below), archive_score,
                        )
            else:
                # Above threshold — clear any existing watermark
                if watermark is not None and access_stats:
                    access_stats.set_watermark(rel, None)

        logger.info("CuratorJob digest governance: archived %d cards", archived_count)
