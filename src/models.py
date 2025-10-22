from pydantic import BaseModel


class Message(BaseModel):
    message: str
    write_concern: int = 1

class MessageWithId(BaseModel):
    id: int
    message: str
