"""身份入口的公开 OpenAPI 合约。"""
from fastapi import FastAPI


def test_identity_openapi_advertises_form_login_and_http_bearer(production_app: FastAPI) -> None:
    schema = production_app.openapi()
    paths = schema["paths"]
    login = paths["/api/v1/auth/token"]["post"]
    assert "application/x-www-form-urlencoded" in login["requestBody"]["content"]
    security = paths["/api/v1/users/me"]["get"]["security"]
    scheme_name = next(iter(security[0]))
    scheme = schema["components"]["securitySchemes"][scheme_name]
    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
