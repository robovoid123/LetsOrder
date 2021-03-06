from app.models.user import UserRole
from app.api.v1.roles import ROLE_POWER
import os
from fastapi import File, UploadFile, Depends
from PIL import Image as PilImage
import aiofiles
from typing import List
from uuid import uuid4
from fastapi import Depends, HTTPException, status
from app.core.config import settings
from app.core import security
from sqlalchemy.orm import Session
from pydantic import ValidationError
from jose import jwt
from fastapi.security import OAuth2PasswordBearer
from typing import Generator

from app.db.session import SessionLocal

from app.models import User, Image
from app.schemas import TokenPayload, ImageCreate
from app import crud


def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_PREFIX}/auth/access-token"
)


def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(reusable_oauth2)
) -> User:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[security.ALGORITHM]
        )
        token_data = TokenPayload(**payload)
    except (jwt.JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    user = crud.user.get(db, id=token_data.sub)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not crud.user.is_active(current_user):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


IMAGE_OUT_FILE_PATH = f"{settings.STATIC_PATH}/images"
IMAGE_STATIC_URL_PATH = f"{settings.STATIC_URL}/images"


def compress(filepath: str, size: int = 600):
    """
    alter the size of image in the given filepath
    """
    s = (size, size)
    img = PilImage.open(filepath)
    img.thumbnail(s, PilImage.ANTIALIAS)
    img.save(filepath, "JPEG")


async def upload_image(db: Session = Depends(get_db), image: UploadFile = File(...)) -> Image:
    """
    upload image and store image url in database
    """
    _, f_ext = os.path.splitext(image.filename)
    filename = str(uuid4()) + f_ext

    out_file_path = f"{IMAGE_OUT_FILE_PATH}/{filename}"
    async with aiofiles.open(out_file_path, 'wb') as out_file:
        while content := await image.read(1024):
            await out_file.write(content)
    db_obj = crud.image.create(db, obj_in=ImageCreate(
        url=f"{IMAGE_STATIC_URL_PATH}/{filename}"
    ))

    compress(out_file_path)
    return db_obj


async def upload_images(db: Session = Depends(get_db),
                        images: List[UploadFile] = File(...)) -> List[Image]:
    """
    upload multiple image and store image url in database
    """
    db_obj_list = []
    for image in images:
        _, f_ext = os.path.splitext(image.filename)
        filename = str(uuid4()) + f_ext
        fp = f"{IMAGE_OUT_FILE_PATH}/{filename}"
        async with aiofiles.open(fp, 'wb') as out_file:
            while content := await image.read(1024):
                await out_file.write(content)
        db_obj = crud.image.create(db, obj_in=ImageCreate(
            url=f"{IMAGE_STATIC_URL_PATH}/{filename}"
        ))
        compress(fp)
        db_obj_list.append(db_obj)
    return db_obj_list


class CheckRole:
    """
    Checks the role of the current logged in user
    This must be kept on each route that needs a permission
    If no permission needed just not using this on a route is 
    recommended.

    Checks power of the role that the current user has and compares
    it with the required power level and only allow if the power level
    of current user is equal or higher
    """

    def __init__(self, role) -> None:
        if role not in UserRole:
            raise Exception
        self._role = role

    def __call__(self, user: User = Depends(get_current_active_user)):
        if ROLE_POWER[user.role] < ROLE_POWER[self._role]:
            raise HTTPException(
                status_code=403, detail="Operation not permitted"
            )
