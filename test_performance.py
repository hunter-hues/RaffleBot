import pytest
import time
from app import app
from models import SessionLocal, User, Giveaway, Base, engine
from unittest.mock import patch

@pytest.fixture(scope="module")
def test_client():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///:memory:"

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db_session = SessionLocal()
    test_user = User(id=1, twitch_id="test_twitch_id", username="test_user")
    db_session.add(test_user)
    db_session.commit()
    db_session.close()

    with app.test_client() as client:
        yield client

def measure_response_time(client, route, method="GET", data=None):
    """Helper function to measure response time for a given route."""
    start_time = time.perf_counter()
    if method == "POST":
        response = client.post(route, data=data)
    else:
        response = client.get(route)
    elapsed_time = time.perf_counter() - start_time
    return response, elapsed_time

def test_response_time_home(test_client):
    print("Testing response time for home route...")
    response, elapsed_time = measure_response_time(test_client, "/")
    print(f"Response time: {elapsed_time:.4f} seconds")
    assert response.status_code == 200
    assert elapsed_time < 0.5, "Home route is too slow!"

def test_response_time_dashboard(test_client):
    print("Testing response time for dashboard route...")
    with test_client.session_transaction() as session:
        session['user_id'] = 1

    response, elapsed_time = measure_response_time(test_client, "/dashboard")
    print(f"Response time: {elapsed_time:.4f} seconds")
    
    if response.status_code != 200:
        print(f"Redirected to: {response.location}") 
    
    assert response.status_code == 200, f"Expected 200, but got {response.status_code}"
    assert elapsed_time < 0.5, "Dashboard route is too slow!"

def test_load_handling(test_client):
    print("Testing load handling for dashboard route...")
    with test_client.session_transaction() as session:
        session['user_id'] = 1

    total_requests = 100
    total_time = 0

    for _ in range(total_requests):
        _, elapsed_time = measure_response_time(test_client, "/dashboard")
        total_time += elapsed_time

    average_time = total_time / total_requests
    print(f"Average response time over {total_requests} requests: {average_time:.4f} seconds")
    assert average_time < 0.5, "Average response time under load is too slow!"

def test_database_query_performance():
    print("Testing database query performance...")
    db_session = SessionLocal()

    for i in range(100):
        db_session.add(Giveaway(title=f"Test Giveaway {i}", frequency=10, threshold=5, creator_id=1))
    db_session.commit()

    start_time = time.perf_counter()
    giveaways = db_session.query(Giveaway).all()
    elapsed_time = time.perf_counter() - start_time

    print(f"Retrieved {len(giveaways)} giveaways in {elapsed_time:.4f} seconds")
    assert len(giveaways) == 100
    assert elapsed_time < 1, "Database query is too slow!"
    db_session.close()
