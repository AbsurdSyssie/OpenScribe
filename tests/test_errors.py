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
