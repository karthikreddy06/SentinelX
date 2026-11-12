import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserUpdate
from app.services.audit_service import record_audit_entry


class UserServiceError(Exception):
    pass


class UserNotFound(UserServiceError):
    pass


class UserAlreadyExists(UserServiceError):
    pass


def get_user_by_id(db: Session, user_id: uuid.UUID) -> User:
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if not user:
        raise UserNotFound(f"User with ID '{user_id}' not found.")
    return user


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.lower())).scalar_one_or_none()


def list_users(
    db: Session,
    limit: int = 20,
    offset: int = 0,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> tuple[list[User], int]:
    query = select(User)
    count_query = select(func.count(User.id))

    conditions = []
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active == is_active)

    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)

    total = db.execute(count_query).scalar_one()
    query = query.order_by(User.created_at.desc(), User.id.desc()).limit(limit).offset(offset)
    items = list(db.execute(query).scalars().all())
    return items, total


def create_user(
    db: Session,
    data: UserCreate,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    existing = get_user_by_email(db, data.email)
    if existing:
        raise UserAlreadyExists(f"User with email '{data.email}' already exists.")

    hashed = hash_password(data.password)
    new_user = User(
        email=data.email.lower(),
        hashed_password=hashed,
        full_name=data.full_name,
        role=data.role,
        is_active=True,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    record_audit_entry(
        db=db,
        action="user.create",
        outcome="success",
        user=actor,
        resource_type="user",
        resource_id=str(new_user.id),
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"created_user_email": new_user.email, "role": new_user.role.value},
    )
    return new_user


def update_user(
    db: Session,
    user_id: uuid.UUID,
    data: UserUpdate,
    actor: User | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> User:
    user = get_user_by_id(db, user_id)
    update_dict = data.model_dump(exclude_unset=True)

    changes = {}
    if "role" in update_dict and data.role is not None:
        changes["role"] = {"old": user.role.value, "new": data.role.value}
        user.role = data.role
    if "is_active" in update_dict and data.is_active is not None:
        changes["is_active"] = {"old": user.is_active, "new": data.is_active}
        user.is_active = data.is_active

    db.commit()
    db.refresh(user)

    record_audit_entry(
        db=db,
        action="user.update",
        outcome="success",
        user=actor,
        resource_type="user",
        resource_id=str(user.id),
        ip_address=ip_address,
        user_agent=user_agent,
        metadata={"changes": changes},
    )
    return user
