from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from biliup.database.models import Configuration

from ..dependencies import get_session
from ..schemas import Credentials

router = APIRouter()
hasher = PasswordHasher()


def _users(session: Session) -> list[Configuration]:
    return list(session.scalars(select(Configuration).where(Configuration.key == "biliup")))


@router.get("/v1/users/biliup")
def user_exists(session: Session = Depends(get_session)) -> Response:
    return Response(status_code=200 if _users(session) else 404)


@router.post("/v1/users/register")
def register(credentials: Credentials, request: Request, session: Session = Depends(get_session)) -> Response:
    if _users(session):
        raise HTTPException(409, "user already exists")
    row = Configuration(key="biliup", value=hasher.hash(credentials.password))
    session.add(row)
    session.commit()
    session.refresh(row)
    request.session["user_id"] = row.id
    return Response(status_code=200)


@router.post("/v1/users/login")
def login(credentials: Credentials, request: Request, session: Session = Depends(get_session)) -> Response:
    for user in reversed(_users(session)):
        try:
            if hasher.verify(user.value, credentials.password):
                request.session["user_id"] = user.id
                return Response(status_code=200)
        except (VerifyMismatchError, ValueError):
            continue
    raise HTTPException(401, "invalid credentials")


@router.get("/v1/logout")
def logout(request: Request) -> Response:
    request.session.clear()
    return Response(status_code=204)
