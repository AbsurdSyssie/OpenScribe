Below are the diffs I would implement on top of `81f9925b5617694f7b37cc19f2711193b3893111`.

I would keep this as a small hardening patch, not a larger redesign. The commit already added `credential_action` to STT/LLM schemas and browser routes, and removed STT/LLM `preserved_bearer_token` defaults.   

---

## 1. STT: do not delete an existing config when a replacement credential is bad

The important safety rule: **a failed replacement credential should not destroy the existing provider config**.

```diff
diff --git a/app/services/stt.py b/app/services/stt.py
index b0c3d77..PATCH 100644
--- a/app/services/stt.py
+++ b/app/services/stt.py
@@
 def upsert_stt_config(db: Session, actor: User, payload: SttConfigUpsert) -> TeamSttConfig:
     team = _resolve_admin_scoped_team(db, actor, team_id=payload.team_id)
     config = None
@@
     creating = config is None
     replacing_secret = payload.credential_action == "replace" or bool(payload.bearer_token)
     removing_secret = payload.credential_action == "remove"
+
+    existing_vault_secret_ref = config.vault_secret_ref if config is not None else ""
+    existing_credential_fingerprint = config.credential_fingerprint if config is not None else None
+    existing_credential_status = config.credential_status if config is not None else ProviderCredentialStatus.unknown
+    existing_inspection_metadata = dict(config.inspection_metadata_json or {}) if config is not None else {}
+
     if removing_secret and payload.bearer_token:
         raise AppError(422, "business_rule_violation", "Bearer token cannot be supplied when credential_action is remove", {"field": "credential_action"})
     if replacing_secret and not payload.bearer_token:
         raise AppError(422, "business_rule_violation", "Bearer token is required when credential_action is replace", {"field": "bearer_token"})
@@
-    if payload.bearer_token:
-        config.vault_secret_ref = write_team_stt_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
-        config.credential_status = ProviderCredentialStatus.pending_inspection
+    if replacing_secret and payload.bearer_token:
+        config.credential_status = ProviderCredentialStatus.pending_inspection
     elif removing_secret:
-        if config.vault_secret_ref:
-            delete_after_commit = True
+        if config.vault_secret_ref:
+            delete_after_commit = True
         config.vault_secret_ref = ""
+        config.credential_fingerprint = None
+        config.credential_status = ProviderCredentialStatus.unknown
+        config.inspection_metadata_json = {"status": "unknown", "reason": "credential_removed"}
     elif payload.adapter_kind in {SttAdapterKind.generic_rest, SttAdapterKind.openai_compatible_rest} and creating:
         config.vault_secret_ref = ""
         config.credential_fingerprint = None
         config.credential_status = ProviderCredentialStatus.unknown
@@
-    if payload.bearer_token:
+    if replacing_secret and payload.bearer_token:
         try:
-            inspection = inspect_stt_contract(
-                db,
-                actor,
-                SttInspectRequest(team_id=team.id, adapter_kind=payload.adapter_kind, base_url=payload.base_url, bearer_token=payload.bearer_token),
-            )
+            if payload.adapter_kind is SttAdapterKind.generic_rest:
+                _verify_generic_stt_config_with_sample(config, bearer_token=payload.bearer_token)
+                inspection = None
+            else:
+                inspection = inspect_stt_contract(
+                    db,
+                    actor,
+                    SttInspectRequest(
+                        team_id=team.id,
+                        adapter_kind=payload.adapter_kind,
+                        base_url=payload.base_url,
+                        bearer_token=payload.bearer_token,
+                    ),
+                )
         except AppError as exc:
             if _is_credential_rejection(exc):
-                secret_config_id = config.id
-                db.delete(config)
-                db.commit()
-                try:
-                    delete_team_stt_bearer_token(team_id=team.id, config_id=secret_config_id)
-                except AppError as cleanup_exc:
-                    logger.warning(
-                        "stt_config_invalid_secret_cleanup_failed",
-                        extra={"config_id": str(secret_config_id), "team_id": str(team.id), "error_code": cleanup_exc.code},
-                    )
+                if creating:
+                    db.rollback()
+                else:
+                    config.vault_secret_ref = existing_vault_secret_ref
+                    config.credential_fingerprint = existing_credential_fingerprint
+                    config.credential_status = existing_credential_status
+                    config.inspection_metadata_json = existing_inspection_metadata
+                    db.add(config)
+                    db.rollback()
                 raise AppError(422, "provider_credential_invalid", "STT provider rejected the supplied credential", {"provider_type": "stt"}) from exc
             config.credential_status = ProviderCredentialStatus.partial
             config.inspection_metadata_json = {"status": "partial", "warning": exc.message, "error_code": exc.code}
         else:
-            if inspection.available_models:
+            config.vault_secret_ref = write_team_stt_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
+            config.credential_fingerprint = fingerprint
+            if inspection is not None and inspection.available_models:
                 config.available_models_json = list(inspection.available_models)
-            config.credential_status = _inspection_status(inspection, had_secret=True)
-            config.inspection_metadata_json = _status_metadata_from_inspection(inspection, status=config.credential_status)
+            if inspection is not None:
+                config.credential_status = _inspection_status(inspection, had_secret=True)
+                config.inspection_metadata_json = _status_metadata_from_inspection(inspection, status=config.credential_status)
+            else:
+                config.credential_status = ProviderCredentialStatus.verified
+                config.inspection_metadata_json = {
+                    "status": ProviderCredentialStatus.verified.value,
+                    "adapter_kind": payload.adapter_kind.value,
+                    "verification": "synthetic_stt_sample",
+                }
```

Why: the current direction is right, but replacement credentials should be validated before the existing config is made unrecoverable. The STT service already has `_verify_generic_stt_config_with_sample`, which is exactly the right primitive for generic REST validation. 

---

## 2. STT: make `remove` clear all credential-derived state

This is a smaller explicit cleanup. If the implementation already partially does this lower in the file, I would still make it uniform.

```diff
diff --git a/app/services/stt.py b/app/services/stt.py
index b0c3d77..PATCH 100644
--- a/app/services/stt.py
+++ b/app/services/stt.py
@@
     elif removing_secret:
         if config.vault_secret_ref:
             delete_after_commit = True
         config.vault_secret_ref = ""
+        config.credential_fingerprint = None
+        config.credential_status = ProviderCredentialStatus.unknown
+        config.inspection_metadata_json = {
+            "status": ProviderCredentialStatus.unknown.value,
+            "reason": "credential_removed",
+        }
```

---

## 3. LLM: make secret removal fail closed if Vault cleanup fails

Currently, LLM removal clears the DB ref and then tries Vault cleanup after commit. I would flip that: if Vault cleanup fails, do not commit a DB state that says the secret is gone.

```diff
diff --git a/app/services/llm.py b/app/services/llm.py
index 7930f3e..PATCH 100644
--- a/app/services/llm.py
+++ b/app/services/llm.py
@@
-    delete_after_commit = False
     if replacing_secret and payload.bearer_token:
         config.vault_secret_ref = write_team_llm_bearer_token(team_id=team.id, config_id=config.id, bearer_token=payload.bearer_token)
     elif removing_secret:
         if config.vault_secret_ref:
-            delete_after_commit = True
+            delete_team_llm_bearer_token(team_id=team.id, config_id=config.id)
         config.vault_secret_ref = ""
@@
     db.commit()
-    if delete_after_commit:
-        try:
-            delete_team_llm_bearer_token(team_id=team.id, config_id=config.id)
-        except AppError as exc:
-            logger.warning("llm_config_secret_cleanup_failed", extra={"config_id": str(config.id), "team_id": str(team.id), "error_code": exc.code})
     db.refresh(config)
     return config
```

Rationale: orphaning a token in Vault while the DB says no token exists is worse than failing the remove operation.

---

## 4. Admin template: explicit credential copy

I would add this near both STT and LLM credential fields.

```diff
diff --git a/app/templates/admin.html b/app/templates/admin.html
index 1f31e52..PATCH 100644
--- a/app/templates/admin.html
+++ b/app/templates/admin.html
@@
           <label>
             Bearer token
             <input type="password" name="bearer_token" autocomplete="off">
+            <small>
+              Tokens are never retained after inspection responses. Re-enter a token to save or replace credentials.
+              When editing an existing provider, choose “keep existing credential” to leave the saved token unchanged.
+            </small>
           </label>
@@
           <label>
             Credential action
             <select name="credential_action">
               <option value="keep" {% if stt_form.credential_action == "keep" %}selected{% endif %}>Keep existing credential</option>
               <option value="replace" {% if stt_form.credential_action == "replace" %}selected{% endif %}>Replace credential</option>
               <option value="remove" {% if stt_form.credential_action == "remove" %}selected{% endif %}>Remove credential</option>
             </select>
           </label>
```

And same idea for the LLM form:

```diff
diff --git a/app/templates/admin.html b/app/templates/admin.html
index 1f31e52..PATCH 100644
--- a/app/templates/admin.html
+++ b/app/templates/admin.html
@@
           <label>
             Bearer token
             <input type="password" name="bearer_token" autocomplete="off">
+            <small>
+              Tokens are never retained after model discovery responses. Re-enter a token to save or replace credentials.
+              When editing an existing provider, choose “keep existing credential” to leave the saved token unchanged.
+            </small>
           </label>
@@
           <label>
             Credential action
             <select name="credential_action">
               <option value="keep" {% if llm_form.credential_action == "keep" %}selected{% endif %}>Keep existing credential</option>
               <option value="replace" {% if llm_form.credential_action == "replace" %}selected{% endif %}>Replace credential</option>
               <option value="remove" {% if llm_form.credential_action == "remove" %}selected{% endif %}>Remove credential</option>
             </select>
           </label>
```

The exact insertion point depends on the current template block layout, but I would add this copy wherever the token and credential-action controls already render.

---

## 5. Tests: credential-action behavior

I would add focused tests. Sketch below assumes existing fixtures/helpers from `tests/test_api.py`.

```diff
diff --git a/tests/test_api.py b/tests/test_api.py
index 2c5a8b1..PATCH 100644
--- a/tests/test_api.py
+++ b/tests/test_api.py
@@
 def test_stt_config_confirmed_duplicate_can_proceed(client, db_session, make_team, make_user):
     ...
+
+
+def test_stt_credential_action_keep_preserves_existing_secret(client, db_session, make_team, make_user, monkeypatch):
+    team = make_team(name="Clinic North")
+    make_user(email="admin-stt-keep@example.com", password="password-1", is_system_admin=True)
+    login(client, email="admin-stt-keep@example.com", password="password-1")
+
+    created = client.post(
+        "/api/v1/stt-configs",
+        json={
+            "team_id": str(team.id),
+            "label": "Clinic STT",
+            "adapter_kind": "openai_compatible_rest",
+            "base_url": "http://127.0.0.1:7000",
+            "bearer_token": "first-secret",
+            "credential_action": "replace",
+            "model_name": "whisper-1",
+        },
+    )
+    assert created.status_code == 200
+    config_id = created.json()["id"]
+    persisted = db_session.get(TeamSttConfig, UUID(config_id))
+    original_ref = persisted.vault_secret_ref
+    original_fingerprint = persisted.credential_fingerprint
+
+    writes = []
+    monkeypatch.setattr(
+        "app.services.stt.write_team_stt_bearer_token",
+        lambda **kwargs: writes.append(kwargs["bearer_token"]) or "secret:unexpected",
+    )
+
+    updated = client.post(
+        "/api/v1/stt-configs",
+        json={
+            "config_id": config_id,
+            "team_id": str(team.id),
+            "label": "Clinic STT renamed",
+            "adapter_kind": "openai_compatible_rest",
+            "base_url": "http://127.0.0.1:7000",
+            "credential_action": "keep",
+            "model_name": "whisper-1",
+        },
+    )
+
+    assert updated.status_code == 200
+    db_session.refresh(persisted)
+    assert persisted.vault_secret_ref == original_ref
+    assert persisted.credential_fingerprint == original_fingerprint
+    assert writes == []
+
+
+def test_stt_credential_action_remove_clears_optional_generic_secret(client, db_session, make_team, make_user, monkeypatch):
+    team = make_team(name="Clinic North")
+    make_user(email="admin-stt-remove@example.com", password="password-1", is_system_admin=True)
+    login(client, email="admin-stt-remove@example.com", password="password-1")
+
+    created = client.post(
+        "/api/v1/stt-configs",
+        json={
+            "team_id": str(team.id),
+            "label": "Generic STT",
+            "adapter_kind": "generic_rest",
+            "base_url": "http://127.0.0.1:7000",
+            "transcribe_path": "/transcribe",
+            "file_field_name": "audio_file",
+            "response_text_path": "text",
+            "bearer_token": "secret-to-remove",
+            "credential_action": "replace",
+        },
+    )
+    assert created.status_code == 200
+    config_id = created.json()["id"]
+
+    deleted = []
+    monkeypatch.setattr(
+        "app.services.stt.delete_team_stt_bearer_token",
+        lambda **kwargs: deleted.append(kwargs["config_id"]),
+    )
+
+    removed = client.post(
+        "/api/v1/stt-configs",
+        json={
+            "config_id": config_id,
+            "team_id": str(team.id),
+            "label": "Generic STT",
+            "adapter_kind": "generic_rest",
+            "base_url": "http://127.0.0.1:7000",
+            "transcribe_path": "/transcribe",
+            "file_field_name": "audio_file",
+            "response_text_path": "text",
+            "credential_action": "remove",
+        },
+    )
+
+    assert removed.status_code == 200
+    persisted = db_session.get(TeamSttConfig, UUID(config_id))
+    assert persisted.vault_secret_ref == ""
+    assert persisted.credential_fingerprint is None
+    assert persisted.credential_status is ProviderCredentialStatus.unknown
+    assert deleted == [persisted.id]
+
+
+def test_stt_replacing_bad_existing_credential_does_not_delete_config(client, db_session, make_team, make_user, monkeypatch):
+    team = make_team(name="Clinic North")
+    make_user(email="admin-stt-bad-replace@example.com", password="password-1", is_system_admin=True)
+    login(client, email="admin-stt-bad-replace@example.com", password="password-1")
+
+    created = client.post(
+        "/api/v1/stt-configs",
+        json={
+            "team_id": str(team.id),
+            "label": "Clinic STT",
+            "adapter_kind": "openai_compatible_rest",
+            "base_url": "http://127.0.0.1:7000",
+            "bearer_token": "valid-secret",
+            "credential_action": "replace",
+            "model_name": "whisper-1",
+        },
+    )
+    assert created.status_code == 200
+    config_id = UUID(created.json()["id"])
+
+    def reject_inspection(*args, **kwargs):
+        raise AppError(502, "stt_request_failed", "STT provider request failed", {"status_code": 401})
+
+    monkeypatch.setattr("app.services.stt.inspect_stt_contract", reject_inspection)
+
+    rejected = client.post(
+        "/api/v1/stt-configs",
+        json={
+            "config_id": str(config_id),
+            "team_id": str(team.id),
+            "label": "Clinic STT",
+            "adapter_kind": "openai_compatible_rest",
+            "base_url": "http://127.0.0.1:7000",
+            "bearer_token": "bad-secret",
+            "credential_action": "replace",
+            "model_name": "whisper-1",
+        },
+    )
+
+    assert_error(
+        rejected,
+        status_code=422,
+        code="provider_credential_invalid",
+        message="STT provider rejected the supplied credential",
+    )
+    assert db_session.get(TeamSttConfig, config_id) is not None
+
+
+def test_llm_credential_action_remove_rejected_for_openai(client, make_team, make_user):
+    team = make_team(name="Clinic North")
+    make_user(email="admin-llm-remove@example.com", password="password-1", is_system_admin=True)
+    login(client, email="admin-llm-remove@example.com", password="password-1")
+
+    response = client.post(
+        "/api/v1/llm-configs",
+        json={
+            "team_id": str(team.id),
+            "label": "OpenAI",
+            "adapter_kind": "openai_chat",
+            "base_url": "https://api.openai.com/v1",
+            "model_name": "gpt-4o-mini",
+            "credential_action": "remove",
+        },
+    )
+
+    assert_error(
+        response,
+        status_code=422,
+        code="business_rule_violation",
+        message="OpenAI and Bedrock LLM configs require a saved bearer token",
+    )
+
+
+def test_llm_credential_action_keep_preserves_existing_secret(client, db_session, make_team, make_user, monkeypatch):
+    team = make_team(name="Clinic North")
+    make_user(email="admin-llm-keep@example.com", password="password-1", is_system_admin=True)
+    login(client, email="admin-llm-keep@example.com", password="password-1")
+
+    created = client.post(
+        "/api/v1/llm-configs",
+        json={
+            "team_id": str(team.id),
+            "label": "OpenAI",
+            "adapter_kind": "openai_chat",
+            "base_url": "https://api.openai.com/v1",
+            "bearer_token": "first-secret",
+            "credential_action": "replace",
+            "model_name": "gpt-4o-mini",
+        },
+    )
+    assert created.status_code == 200
+    config_id = created.json()["id"]
+    persisted = db_session.get(TeamLlmConfig, UUID(config_id))
+    original_ref = persisted.vault_secret_ref
+
+    writes = []
+    monkeypatch.setattr(
+        "app.services.llm.write_team_llm_bearer_token",
+        lambda **kwargs: writes.append(kwargs["bearer_token"]) or "secret:unexpected",
+    )
+
+    updated = client.post(
+        "/api/v1/llm-configs",
+        json={
+            "config_id": config_id,
+            "team_id": str(team.id),
+            "label": "OpenAI renamed",
+            "adapter_kind": "openai_chat",
+            "base_url": "https://api.openai.com/v1",
+            "credential_action": "keep",
+            "model_name": "gpt-4o-mini",
+        },
+    )
+
+    assert updated.status_code == 200
+    db_session.refresh(persisted)
+    assert persisted.vault_secret_ref == original_ref
+    assert writes == []
```

The test function names can be adjusted to match the file’s organization.

---

## 6. Optional: template test for no hidden token preservation

I would also add a cheap HTML-level regression test.

```diff
diff --git a/tests/test_admin_ui.py b/tests/test_admin_ui.py
index CURRENT..PATCH 100644
--- a/tests/test_admin_ui.py
+++ b/tests/test_admin_ui.py
@@
+def test_admin_stt_and_llm_forms_do_not_render_preserved_bearer_token(client, make_user):
+    make_user(email="admin-token-form@example.com", password="password-1", is_system_admin=True)
+    login(client, email="admin-token-form@example.com", password="password-1")
+
+    response = client.get("/admin")
+
+    assert response.status_code == 200
+    assert "preserved_bearer_token" not in response.text
+    assert 'name="credential_action"' in response.text
```

I would keep de-identification out of this test unless that flow is also being changed, because the current presentation still uses `preserved_bearer_token` for de-identification defaults. 

---

## What I would not put in this patch

I would **not** add full LLM persisted credential status in the same diff. That is a separate migration and lifecycle feature. The current commit already improves LLM `keep | replace | remove`; persisted LLM health can be a follow-up.

My merge-blocking diffs are the STT replacement safety and the credential-action tests.
