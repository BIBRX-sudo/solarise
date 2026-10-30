import os
import pytest

# Ensure required test environment defaults without touching .env
os.environ.setdefault('ADMIN_API_KEY', 'testadminkey')
os.environ.setdefault('JWT_SECRET', 'testjwtsecret')
os.environ.setdefault('OPENWA_URL', 'http://127.0.0.1:2785')
os.environ.setdefault('OPENWA_API_KEY', 'test_openwa_key')
os.environ.setdefault('OPENWA_SESSION_ID', 'test_session')
os.environ.setdefault('WEBHOOK_SECRET', 'test_webhook_secret')
os.environ.setdefault('ALLOWED_TEST_PHONE', '*')
os.environ['GROQ_API_KEY'] = ''  # Mock Groq in tests; prevent live API calls during pytest


@pytest.fixture(autouse=True)
def isolate_test_database_and_state(tmp_path):
    """Global fixture ensuring all tests use an isolated database and clean in-memory state.
    Protects production leads in solarise_leads.db from any test writes.
    """
    import leads
    original_db = leads.DB_NAME
    temp_db_path = tmp_path / "global_test_isolated.db"
    temp_db_path.touch()
    leads.DB_NAME = str(temp_db_path)
    leads.initialize_database()

    # Reset in-memory conversation and deduplication state
    try:
        import main
        main.conversation_state.clear()
        main.processed_message_ids.clear()
        main.processed_message_order.clear()
    except Exception:
        pass

    try:
        yield
    finally:
        leads.DB_NAME = original_db
        try:
            import main
            main.conversation_state.clear()
            main.processed_message_ids.clear()
            main.processed_message_order.clear()
        except Exception:
            pass
