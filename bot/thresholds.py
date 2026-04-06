"""Threshold-based audio selection, ported from listen-timer.html."""

# Threshold table: for each reaction, maps minimum count to (audio_file, volume)
# Multiple thresholds can be active simultaneously (layered audio)
THRESHOLDS = {
    'clap': [
        (3,  'clap_one',     0.6),
        (5,  'clap_few',     0.6),
        (10, 'clap_alot',    0.6),
        (20, 'clap_whistle', 0.7),
    ],
    'laugh': [
        (3,  'laugh', 0.2),
        (5,  'laugh', 0.4),
        (10, 'laugh', 0.7),
    ],
    'boo': [
        (10, 'boo', 0.2),
        (15, 'boo', 0.4),
        (30, 'boo', 0.7),
    ],
    'cry': [
        (3,  'cry_one',  0.1),
        (5,  'cry_one',  0.1),
        (10, 'cry_alot', 0.7),
    ],
    'woo': [
        (3,  'woo_one',  0.2),
        (5,  'woo_alot', 0.4),
        (10, 'woo_alot', 0.7),
    ],
}


def evaluate(actions_count: dict) -> list[tuple[str, float]]:
    """Given current action counts, return all matching (audio_filename, volume) pairs.

    For each reaction type, collects ALL thresholds that the current count
    meets or exceeds. Multiple thresholds fire simultaneously to produce
    layered audio (e.g. clap at 10 plays both clap_one and clap_alot).

    When the same filename appears at multiple thresholds, only the highest
    volume is kept (e.g. laugh at 10 plays laugh at 0.7, not 0.2+0.4+0.7).

    Returns:
        List of (filename, volume) tuples. filename has no extension — the
        mixer resolves it to the pre-converted PCM file.
    """
    result = []
    for reaction, thresholds in THRESHOLDS.items():
        count = actions_count.get(reaction, 0)
        if count <= 0:
            continue
        # Collect all matching thresholds, dedup by filename keeping highest volume
        active: dict[str, float] = {}
        for min_count, filename, volume in thresholds:
            if count >= min_count:
                if filename not in active or volume > active[filename]:
                    active[filename] = volume
        result.extend(active.items())
    return result
