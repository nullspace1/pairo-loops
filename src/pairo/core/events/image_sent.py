"""Image input event."""

from pydantic import BaseModel


class ImageSent(BaseModel):
    """The user has supplied an image path to the host application."""

    image_path: str

