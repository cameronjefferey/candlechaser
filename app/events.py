from dataclasses import dataclass, field


@dataclass
class Event:
    """Normalized unit of information; every source emits these."""

    source: str        # "news" | "filing" | "halt" | "options"
    source_id: str     # unique id within the source (wire id, accession number, etc.)
    ts: float          # time.time() when we received it
    text: str          # headline / filing summary / halt description
    symbols: list[str] = field(default_factory=list)  # tagged symbols from the source
    url: str = ""
    meta: dict = field(default_factory=dict)  # source-specific extras
