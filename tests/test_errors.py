def test_team_and_user_lists_return_plain_arrays(client, make_user):
    make_user(email="admin@example.com", password="password-1", is_system_admin=True)
    client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "password-1"})

    teams_response = client.get("/api/v1/teams")
    users_response = client.get("/api/v1/users")

    assert teams_response.status_code == 200
    assert users_response.status_code == 200
    assert teams_response.json() == []
    assert isinstance(users_response.json(), list)
    assert users_response.json()[0]["email"] == "admin@example.com"


def test_validation_error_response_does_not_disclose_schema_details(client):
    response = client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "short"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"] == "Request validation failed"
    assert body["error"]["details"] == {"issue_count": 2}
    assert "value_error" not in str(body)
    assert "body.email" not in str(body)


def test_rate_limit_response_includes_retry_after(client, make_user):
    make_user(email="rate-retry@example.com", password="password-1", mfa_required=False, mfa_enabled=False)

    responses = [
        client.post("/api/v1/auth/login", json={"email": "rate-retry@example.com", "password": f"wrong-password-{attempt}"})
        for attempt in range(6)
    ]

    assert responses[-1].status_code == 429
    assert responses[-1].headers["Retry-After"] == "300"
