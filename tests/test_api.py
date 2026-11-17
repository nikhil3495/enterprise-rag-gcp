from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from rag.api.main import create_app
from rag.config import Settings
from rag.graph.nodes import GraphDeps
from rag.graph.service import RAGService

from .conftest import make_settings
from .test_graph import FakeReasoner, FakeRetriever, FakeSql

URL = "https://rag-api-123.us-central1.run.app"


def service(settings: Settings, **kw: Any) -> RAGService:
    deps = GraphDeps(
        reasoner=FakeReasoner(**kw), retriever=FakeRetriever(), sql_tool=FakeSql(), catalog_summary=""
    )
    return RAGService(deps, settings)


def verifier(token: str, audience: list[str]) -> dict[str, Any]:
    if token == "expired":
        raise ValueError("Token expired")
    aud, email = token.split("|")
    if aud not in audience:
        raise ValueError("Token has wrong audience")
    return {
        "iss": "https://accounts.google.com",
        "aud": aud,
        "email": email,
        "email_verified": True,
        "sub": "1",
    }


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, service=service(settings)))


def test_health_ready_info(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    assert client.get("/v1/info").json()["profile"] == "local"


def test_query_schema_and_request_id(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "How much PTO?"}, headers={"X-Request-ID": "abc-123"})
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "abc-123"
    body = r.json()
    assert body["request_id"] == "abc-123"
    assert set(body) >= {
        "answer",
        "citations",
        "route",
        "grounded",
        "insufficient_context",
        "usage",
        "latency_ms",
    }
    assert set(body["usage"]) == {
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cost_usd",
        "output_tokens_per_s",
    }
    assert "trace" not in body and "retrieved" not in body


def test_query_debug_includes_trace_and_passages(client: TestClient) -> None:
    body = client.post("/v1/query", json={"question": "q", "debug": True}).json()
    assert body["trace"][0]["node"] == "route"
    assert body["retrieved"][0]["source_uri"] == "data/docs/employee_handbook.md"
    assert body["context"].startswith("[S1]")


def test_generated_request_id_and_bad_header(client: TestClient) -> None:
    r = client.post("/v1/query", json={"question": "q"}, headers={"X-Request-ID": "bad id with spaces!"})
    assert r.headers["x-request-id"] != "bad id with spaces!" and len(r.headers["x-request-id"]) == 32


@pytest.mark.parametrize(
    "payload", [{"question": ""}, {"question": "x" * 2001}, {"question": "q", "top_k": 0}, {}]
)
def test_validation_errors(client: TestClient, payload: dict[str, Any]) -> None:
    r = client.post("/v1/query", json=payload)
    assert r.status_code == 422 and r.json()["error"] == "invalid_request" and r.json()["request_id"]


def test_upstream_failure_returns_503_without_stack_trace(settings: Settings) -> None:
    class Broken(RAGService):
        def answer(self, *a: Any, **k: Any) -> Any:
            raise RuntimeError("secret internal detail")

    deps = GraphDeps(
        reasoner=FakeReasoner(), retriever=FakeRetriever(), sql_tool=FakeSql(), catalog_summary=""
    )
    c = TestClient(create_app(settings, service=Broken(deps, settings)))
    r = c.post("/v1/query", json={"question": "q"}, headers={"X-Request-ID": "rid-503"})
    assert r.status_code == 503
    assert r.json() == {
        "error": "upstream_unavailable",
        "detail": "The assistant is temporarily unavailable. Retry later.",
        "request_id": "rid-503",
    }
    assert "secret" not in r.text


# ---- IAM mode -------------------------------------------------------------------------------------
@pytest.fixture
def iam_client(tmp_path: Path) -> TestClient:
    s = make_settings(
        tmp_path,
        auth_mode="iam",
        auth_audience=f"{URL},cli-client-id",
        auth_allowed_principals="owner@example.com, ci@proj.iam.gserviceaccount.com",
    )
    return TestClient(create_app(s, service=service(s), verifier=verifier))


def test_iam_missing_token_401(iam_client: TestClient) -> None:
    r = iam_client.post("/v1/query", json={"question": "q"})
    assert r.status_code == 401 and r.json()["error"] == "unauthenticated"
    assert r.headers["www-authenticate"] == "Bearer"
    assert (
        iam_client.post("/v1/query", json={"question": "q"}, headers={"Authorization": "Basic x"}).status_code
        == 401
    )


def test_iam_bad_or_expired_token_401(iam_client: TestClient) -> None:
    r = iam_client.post("/v1/query", json={"question": "q"}, headers={"Authorization": "Bearer expired"})
    assert r.status_code == 401 and r.json()["detail"] == "invalid token"


def test_iam_wrong_audience_401(iam_client: TestClient) -> None:
    r = iam_client.post(
        "/v1/query",
        json={"question": "q"},
        headers={"Authorization": "Bearer https://other.run.app|owner@example.com"},
    )
    assert r.status_code == 401


def test_iam_allowlist(iam_client: TestClient) -> None:
    denied = iam_client.post(
        "/v1/query", json={"question": "q"}, headers={"Authorization": f"Bearer {URL}|mallory@example.com"}
    )
    assert denied.status_code == 403 and denied.json()["error"] == "forbidden"
    ok = iam_client.post(
        "/v1/query", json={"question": "q"}, headers={"Authorization": f"Bearer {URL}|Owner@Example.com"}
    )
    assert ok.status_code == 200
    cli = iam_client.get(
        "/v1/info", headers={"Authorization": "Bearer cli-client-id|ci@proj.iam.gserviceaccount.com"}
    )
    assert cli.status_code == 200
    assert iam_client.get("/healthz").status_code == 200  # probes stay unauthenticated


def test_iam_requires_audience(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="AUDIENCE"):
        create_app(make_settings(tmp_path, auth_mode="iam"))


def test_iam_rejects_non_google_issuer(tmp_path: Path) -> None:
    s = make_settings(tmp_path, auth_mode="iam", auth_audience=URL)
    app = create_app(s, service=service(s), verifier=lambda t, a: {"iss": "https://evil", "aud": URL})
    assert TestClient(app).get("/v1/info", headers={"Authorization": "Bearer t"}).status_code == 401


def test_lifespan_builds_local_container(ingested) -> None:  # type: ignore[no-untyped-def]
    app = create_app(ingested.settings, container=ingested)
    with TestClient(app) as c:
        body = c.post("/v1/query", json={"question": "What is the minimum password length?"}).json()
        assert "14" in body["answer"] and body["citations"]
        assert c.get("/v1/info").json()["vector_backend"] == "memory"
