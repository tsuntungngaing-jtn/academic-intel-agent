"""Load local environment from ``.env`` when ``python-dotenv`` is installed."""


def load_environment() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
