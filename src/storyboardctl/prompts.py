from __future__ import annotations

from collections.abc import Sequence

from storyboardctl.models import DialogueCueSpec


def h3_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    minutes, remainder = divmod(milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def _dialogue_lines(dialogue: Sequence[DialogueCueSpec]) -> str:
    lines: list[str] = []
    for cue in dialogue:
        start = h3_timestamp(cue.start_seconds)
        end = h3_timestamp(cue.end_seconds)
        lines.append(
            f"At {start}, {cue.speaker} ({cue.speaker_id}) says: "
            f"<d>[{cue.language}] {cue.text}</d> The dialogue ends by {end}; "
            f"{cue.speaker}'s lips close when the line ends."
        )
    return "\n".join(lines)


def compile_h3_prompt(
    prompt: str,
    *,
    dialogue: Sequence[DialogueCueSpec] = (),
    negative_prompt: str | None = None,
) -> str:
    if not dialogue:
        return f"{prompt}\n\nAVOID: {negative_prompt}" if negative_prompt else prompt

    cues = _dialogue_lines(dialogue)
    if "integrated_multimodal_description:" in prompt:
        markers = [
            index for marker in ("overall_soundscape:", "non_diegetic_music:") if (index := prompt.find(marker)) >= 0
        ]
        insert_at = min(markers) if markers else len(prompt)
        effective = f"{prompt[:insert_at].rstrip()}\n{cues}\n\n{prompt[insert_at:].lstrip()}".rstrip()
    else:
        effective = (
            f"integrated_multimodal_description: [Shot 1] {prompt}\n{cues}\n\n"
            "overall_soundscape: Scene-appropriate ambience and physical sounds; "
            "the specified dialogue is the only speech.\n\n"
            "non_diegetic_music: N/A"
        )
    if negative_prompt:
        effective = f"{effective}\n\nAVOID: {negative_prompt}"
    return effective
