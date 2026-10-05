"""Approximate batch progress from existing stage events, without extra probing."""


class BatchProgress:
    """Give each input equal weight and keep the displayed fraction monotonic."""

    ANALYSIS = {"prepare": (0, .02), "probe": (.02, .03),
                "snow": (.05, .20), "interlace": (.25, .05)}
    OUTPUT = {"encode": (0, .90), "validate": (.90, .08), "publish": (.98, .02)}

    def __init__(self, count, analyze_only=False):
        self.count = max(1, count)
        self.analyze_only = analyze_only
        self.fraction = 0.0

    def update(self, code, data):
        index = data.get("job_index")
        if index is None:
            return
        local = None
        if code in ("job.completed", "job.analyzed", "job.failed"):
            local = 1.0
        elif code == "progress":
            stage = data.get("stage")
            fraction = data.get("fraction")
            if fraction is None:
                fraction = 1.0 if data.get("state") == "completed" else 0.0
            fraction = min(1.0, max(0.0, fraction))
            if stage in self.ANALYSIS:
                offset, weight = self.ANALYSIS[stage]
                local = offset + weight * fraction
                if self.analyze_only:
                    local /= .30
            elif stage in self.OUTPUT and not self.analyze_only:
                offset, weight = self.OUTPUT[stage]
                parts = max(1, data.get("part_count", 1))
                part = data.get("part_index", 1)
                local = .30 + .70 * ((part - 1 + offset + weight * fraction) / parts)
        if local is not None:
            self.fraction = max(self.fraction, min(.999, (index - 1 + local) / self.count))

    def estimated_total(self, elapsed):
        """Avoid extrapolating the tiny startup fraction into a misleading ETA."""
        if elapsed < 2 or self.fraction < .05:
            return None
        return elapsed / self.fraction


def duration_text(seconds):
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
