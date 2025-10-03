from pydantic import BaseModel


class Message(BaseModel):
    message: str

class MessageWithId(BaseModel):
    id: int
    message: str
