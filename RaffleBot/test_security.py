import pytest
from app import app
from models import SessionLocal, User, Base, engine
from unittest.mock import patch

@pytest.fixture(scope="module")
def test_client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///:memory:"

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with app.test_client() as client:
        yield client

def test_unauthorized_access_dashboard(test_client):
    print("Testing unauthorized access to the dashboard...")
    response = test_client.get('/dashboard')
    assert response.status_code == 302
    assert "/auth/twitch" in response.headers.get("Location", "")
    print("Unauthorized access to the dashboard blocked successfully.")

def test_tampered_session(test_client):
    print("Testing access with tampered session...")
    with test_client.session_transaction() as session:
        session['user_id'] = 9999
    response = test_client.get('/dashboard')
    assert response.status_code == 302
    assert "/auth/twitch" in response.headers.get("Location", "")
    print("Tampered session access blocked successfully.")

def test_sql_injection(test_client):
    print("Testing SQL Injection on user creation...")
    db_session = SessionLocal()

    malicious_username = "' OR '1'='1"
    try:
        user = User(twitch_id="12345", username=malicious_username)
        db_session.add(user)
        db_session.commit()
        assert False, "SQL Injection was successful!"
    except ValueError as e:
        print(f"SQL Injection blocked: {e}")
        db_session.rollback()

    user_count = db_session.query(User).filter_by(username=malicious_username).count()
    db_session.close()
    assert user_count == 0, "SQL Injection was successful!"
    print("SQL Injection test passed.")

def test_xss_protection(test_client):
    print("Testing Cross-Site Scripting (XSS) Protection...")
    xss_payload = "<script>alert('XSS');</script>"

    response = test_client.post('/giveaway/create', data={
        'title': xss_payload,
        'frequency': '10',
        'threshold': '5'
    })

    assert response.status_code == 400, "XSS payload should not be accepted!"
    assert xss_payload not in response.data.decode(), "XSS payload reflected in response!"
    print("XSS Protection test passed.")

def test_rate_limiting(test_client):
    print("Testing rate limiting on dashboard route...")
    max_requests = 10
    blocked = False

    for _ in range(max_requests + 1):
        response = test_client.get('/dashboard')
        if response.status_code == 429:
            blocked = True
            break

    assert blocked, "Rate limiting did not block excessive requests!"
    print("Rate limiting test passed.")

