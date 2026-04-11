THEMES_BY_LEVEL: dict[str, list[str]] = {
    "A1": [],
    "A2": [],
    "B1": [],
    "B2": [],
    "C1": [],
}


def get_session_theme(*, level: str, recent_themes: list[str]) -> str:
    """Pick a theme for the session, respecting cooldown."""
    raise NotImplementedError
