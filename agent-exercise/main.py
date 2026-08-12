from fastapi import FastAPI

from chat import ChatRequest, SESSIONS, TOOL_IMPL, call_gemini_with_retry, grounding_check, log, types

app = FastAPI()


@app.post("/chat")
def chat(req: ChatRequest):
    log.info(f"[{req.session_id}] USER: {req.message}")
    history = SESSIONS.setdefault(req.session_id, [])
    history.append(types.Content(role="user", parts=[types.Part(text=req.message)]))

    try:
        response = call_gemini_with_retry(history)
    except Exception as e:
        log.error(f"[{req.session_id}] Gemini failed after retry: {e}")
        return {"error": "Sorry, I'm having trouble reaching the assistant right now. Please try again shortly."}

    for _ in range(6):
        candidate = response.candidates[0]
        function_calls = [p.function_call for p in candidate.content.parts if p.function_call]
        if not function_calls:
            break
        history.append(candidate.content)
        parts = []
        for fc in function_calls:
            args = dict(fc.args) if fc.args else {}
            log.info(f"[{req.session_id}] TOOL CALL: {fc.name}({args})")
            fn = TOOL_IMPL.get(fc.name)
            try:
                result = fn(**args) if fn else {"error": "unknown_tool"}
            except Exception as e:
                result = {"error": str(e)}
            log.info(f"[{req.session_id}] TOOL RESULT: {result}")
            parts.append(types.Part(function_response=types.FunctionResponse(name=fc.name, response=result)))
        history.append(types.Content(role="user", parts=parts))
        try:
            response = call_gemini_with_retry(history)
        except Exception as e:
            log.error(f"[{req.session_id}] Gemini failed after retry: {e}")
            return {"error": "Sorry, I'm having trouble reaching the assistant right now. Please try again shortly."}

    final_text = response.text or ""
    history.append(types.Content(role="model", parts=[types.Part(text=final_text)]))
    grounding_check(final_text)
    log.info(f"[{req.session_id}] REPLY: {final_text}")
    return {"reply": final_text}