import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, require_roles
from app.database.connection import get_db
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserListResponse, UserResponse, UserUpdate
from app.services.user_service import (
    UserAlreadyExists,
    UserNotFound,
    create_user,
    list_users,
    update_user,
)

router = APIRouter()


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create User (Admin only)",
    description="Provision a new user account with a designated role.",
)
def create_user_endpoint(
    payload: UserCreate,
    request: Request,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> UserResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        user = create_user(
            db=db,
            data=payload,
            actor=current_admin,
            ip_address=ip,
            user_agent=user_agent,
        )
        return UserResponse.model_validate(user)
    except UserAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "",
    response_model=UserListResponse,
    summary="List Users (Admin only)",
    description="Retrieve a paginated list of users with optional filtering.",
)
def list_users_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    role: UserRole | None = Query(default=None),
    is_active: bool | None = Query(default=None),
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> UserListResponse:
    items, total = list_users(
        db=db, limit=limit, offset=offset, role=role, is_active=is_active
    )
    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch(
    "/{id}",
    response_model=UserResponse,
    summary="Update User (Admin only)",
    description="Update a user's role or active status.",
)
def update_user_endpoint(
    id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    current_admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> UserResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        user = update_user(
            db=db,
            user_id=id,
            data=payload,
            actor=current_admin,
            ip_address=ip,
            user_agent=user_agent,
        )
        return UserResponse.model_validate(user)
    except UserNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
