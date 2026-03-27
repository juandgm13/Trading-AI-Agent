from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
    source: str
    title: str
    description: str
    content: str
    url: str
    published_at: datetime
    feed_group: str = "unknown"  # "alpaca"|"<rss source name>"|"Yahoo Finance"
    full_content: str = ""  # Texto completo del artículo (vacío si no se pudo obtener)
