from fastapi import FastAPI
from dancer.api.routes import router
import logging

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Dancer", version="1.0.0")
app.include_router(router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5189)