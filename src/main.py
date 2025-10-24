import os
import asyncio
import logging
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.requests import Request


from models import Message, MessageWithId

app = FastAPI()
templates = Jinja2Templates(directory="templates")
logging.getLogger("uvicorn").setLevel(logging.ERROR)
logging.getLogger("uvicorn.error").setLevel(logging.ERROR)
logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

FOLLOWERS_URL = {
    "http://follower1:8000/follower/append-message": "Follower 1",
    "http://follower2:8000/follower/append-message": "Follower 2"
}

messages = []
message_id = 0
lock = asyncio.Lock()

async def send_to_follower(url: str, message: dict) -> bool:
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json=message, timeout=30)
            logger.info(f"Message sent to {FOLLOWERS_URL[url]}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message to {url}: {e}")

        return False


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    container_name = os.getenv("CONTAINER_NAME")

    async with lock:
        messages_copy = messages.copy()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "container_name": container_name,
            "messages": messages_copy,
        },
    )


@app.post("/master/append-message")
async def append_message(msg: Message):
    global message_id
    if os.getenv("CONTAINER_NAME") != "Master":
        raise HTTPException(
            status_code=403, detail="Only Master container can append messages"
        )
    logger.info(f"Message {msg.message} received in Master node")

    write_concern = msg.w

    async with lock:
        message_id += 1
        entry = {"id": message_id, "message": msg.message}
        messages.append(entry)
        logger.info(f"Message stored in Master node")

    replication_tasks = [
        asyncio.create_task(
            send_to_follower(url, entry)
        ) for url in FOLLOWERS_URL.keys()
    ]

    write_concern -= 1

    if write_concern == 0:
        return {"status": "ok"}

    for future in asyncio.as_completed(replication_tasks):
        result = await future
        if result:
            write_concern -= 1

        if write_concern == 0:
            break

    return {"status": "ok"}


@app.post("/follower/append-message")
async def add_message_follower(msg: MessageWithId):
    if delay_seconds := int(os.getenv("DELAY_SECONDS")):
        await asyncio.sleep(delay_seconds)

    async with lock:
        messages.append(msg.model_dump())

    logger.info(f"Message stored in {os.getenv("CONTAINER_NAME")} node")
    return {"status": "ok"}


@app.get("/list-messages")
async def get_all_messages():
    return {"messages": messages.copy()}
