"""Load local environment from ``.env`` when ``python-dotenv`` is installed."""


def load_environment() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    from core import config

    config.sync_polite_pool_email_from_environment()
