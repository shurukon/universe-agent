# Tiny OpenAI-compatible server: the wizard's custom-provider probe calls
# GET /v1/models; later goals call /v1/chat/completions (canned reply).
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()

@app.get("/v1/models")
async def models():
    return JSONResponse({"object": "list", "data": [{"id": "mock-1", "object": "model"}]})

@app.post("/v1/chat/completions")
async def chat(req: Request):
    return JSONResponse({
        "id": "mock", "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "mock reply"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    })

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8399, log_level="warning")
