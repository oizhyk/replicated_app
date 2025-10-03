# Replicated App


## Run project
```bash
docker compose up --build
```
- http://0.0.0.0:8000/ (master)
- http://0.0.0.0:8001/ (follower 1)
- http://0.0.0.0:8002/ (follower 2)

### FOLLOWER's DELAY
- емуляція затримки встановлюється через env змінну DELAY_SECONDS у 
  docker-compose.yaml

### Rest API
- **`POST /master/append-message`**  
  Додає нове повідомлення у Master і реплікує його всім Followers.  
  **Приклад запиту:**
  ```bash
  curl -X POST http://0.0.0.0:8000/master/append-message \
       -H "Content-Type: application/json" \
       -d '{"message": "Message 1"}'

- **`GET /list-messages`**  
  Повертає всі повідомлення інстанса.
  **Приклад запиту:**
  ```bash
  curl http://0.0.0.0:8000/list-messages
  curl http://0.0.0.0:8001/list-messages
  curl http://0.0.0.0:8002/list-messages

