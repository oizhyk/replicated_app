from pydantic import BaseModel, Field


class Message(BaseModel):
    message: str
    w: int = Field(1, ge=1, description="Write concern parameter")


class MessageWithId(BaseModel):
    id: int
    message: str
