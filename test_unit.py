import pytest
from app import app
from models import SessionLocal, User, Giveaway, Item, Winner, Base, engine
from unittest.mock import patch
from sqlalchemy.exc import IntegrityError

@pytest.fixture(scope="module")
def test_client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///:memory:"
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with app.test_client() as client:
        yield client

@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

def test_home_route(test_client):
    print("Testing home route...")
    response = test_client.get('/')
    print(f"Response data: {response.data.decode()}")
    assert response.status_code == 200
    assert b"Welcome to RaffleBot" in response.data
    print("Home route test passed.")

def test_auth_twitch_redirect(test_client):
    print("Testing Twitch auth redirect...")
    response = test_client.get('/auth/twitch')
    print(f"Redirect Location: {response.headers['Location']}")
    assert response.status_code == 302
    assert "twitch.tv" in response.headers['Location']
    print("Twitch auth redirect test passed.")

@patch('requests.post')
@patch('requests.get')
def test_auth_twitch_callback(mock_get, mock_post, test_client):
    print("Testing Twitch auth callback...")
    mock_post.return_value.json.return_value = {"access_token": "mock_token"}
    mock_get.return_value.json.return_value = {"data": [{"id": "12345", "display_name": "TestUser"}]}
    response = test_client.get('/auth/twitch/callback?code=mock_code')
    print(f"Mocked token response: {mock_post.return_value.json.return_value}")
    print(f"Mocked user response: {mock_get.return_value.json.return_value}")
    with test_client.session_transaction() as session:
        print(f"Session after callback: {session}")
    assert response.status_code == 302
    print("Twitch auth callback test passed.")

def test_dashboard_access_unauthorized(test_client):
    print("Testing unauthorized dashboard access...")
    with test_client.session_transaction() as session:
        session.clear()
        print(f"Session before request: {session}")

    response = test_client.get('/dashboard')
    print(f"Response status code: {response.status_code}")
    print(f"Redirect Location: {response.headers.get('Location')}")
    assert response.status_code == 302
    print("Unauthorized dashboard access test passed.")

def test_dashboard_access_authorized(test_client):
    print("Testing authorized dashboard access...")
    db_session = SessionLocal()
    db_session.add(User(id=1, twitch_id="12345", username="TestUser"))
    db_session.commit()
    db_session.close()
    with test_client.session_transaction() as session:
        session['user_id'] = 1

    response = test_client.get('/dashboard')
    print(f"Response data: {response.data.decode()}")
    assert response.status_code == 200
    assert b"Your Giveaways" in response.data
    print("Authorized dashboard access test passed.")

def test_create_user():
    print("Testing user creation in the database...")
    db_session = SessionLocal()
    print(f"Database state before creation: {db_session.query(User).all()}")
    user = User(twitch_id="12345", username="TestUser")
    db_session.add(user)
    try:
        db_session.commit()
        print("User created successfully.")
    except IntegrityError:
        db_session.rollback()
        print("User creation failed due to IntegrityError.")
    
    retrieved_user = db_session.query(User).filter_by(twitch_id="12345").first()
    print(f"Database state after creation: {db_session.query(User).all()}")
    assert retrieved_user.username == "TestUser"
    db_session.close()
    print("User creation test passed.")

def test_create_giveaway(test_client):
    print("Testing giveaway creation...")
    db_session = SessionLocal()
    db_session.add(User(id=1, twitch_id="12345", username="TestUser"))
    db_session.commit()
    print(f"User in database: {db_session.query(User).filter_by(id=1).first()}")
    db_session.close()

    with test_client.session_transaction() as session:
        session['user_id'] = 1 

    response = test_client.post('/giveaway/create', data={
        'title': 'Test Giveaway',
        'frequency': '10',
        'threshold': '5'
    })
    print(f"Created giveaway: {SessionLocal().query(Giveaway).filter_by(title='Test Giveaway').first()}")
    print(f"Response status code: {response.status_code}")
    assert response.status_code == 302
    print("Giveaway creation test passed.")

def test_create_giveaway_invalid_data(test_client):
    print("Testing giveaway creation with invalid data...")
    with test_client.session_transaction() as session:
        session['user_id'] = 1

    response = test_client.post('/giveaway/create', data={
        'title': '',
        'frequency': 'invalid',
        'threshold': '-1'
    })
    print(f"Response data for invalid input: {response.data.decode()}")
    assert response.status_code == 400
    assert b"Invalid input" in response.data
    print("Giveaway creation with invalid data test passed.")
