"""
Apply Agent — FastAPI backend.

Serves index.html and handles the agent pipeline via SSE streaming.

Run with:
  python app.py
Then open http://localhost:8000
"""

import asyncio
import os
import json
import queue
import shutil
import threading
import uuid
import yaml
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from dotenv import load_dotenv

load_dotenv()

PROFILE_PATH = "profile.yaml"
OUTPUT_DIR = "output"
UPLOAD_DIR = "output/uploads"
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active sessions: session_id → { queue, result }
_sessions: dict[str, dict] = {}
_action_q = queue.Queue()
_result_q = queue.Queue()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    return FileResponse("index.html")


@app.get("/profile")
async def get_profile():
    if not os.path.exists(PROFILE_PATH):
        return {}
    with open(PROFILE_PATH) as f:
        p = yaml.safe_load(f)
    return p


@app.post("/run")
async def run_pipeline(
    resume: UploadFile = File(...),
    cover_letter: UploadFile | None = File(None),
    job_url: str = Form(...),
    tailoring_notes: str = Form(""),
    notice_period: str = Form(""),
    authorized_canada: str = Form(""),
    requires_sponsorship: str = Form(""),
    desired_salary: str = Form(""),
    gender: str = Form(""),
    race_ethnicity: str = Form(""),
    veteran_status: str = Form(""),
    disability_status: str = Form(""),
):
    session_id = str(uuid.uuid4())
    log_q: queue.Queue = queue.Queue()
    _sessions[session_id] = {"queue": log_q, "result": None}

    # Save uploaded resume
    ext = Path(resume.filename).suffix
    resume_path = os.path.join(UPLOAD_DIR, f"{session_id}{ext}")
    with open(resume_path, "wb") as f:
        shutil.copyfileobj(resume.file, f)

    cover_letter_path = None
    if cover_letter:
        cl_ext = Path(cover_letter.filename).suffix
        cover_letter_path = os.path.join(UPLOAD_DIR, f"{session_id}_cl{cl_ext}")
        with open(cover_letter_path, "wb") as f:
            shutil.copyfileobj(cover_letter.file, f)

    # Run pipeline in background thread
    def worker():
        overrides = {
            "notice_period": notice_period,
            "authorized_canada": authorized_canada,
            "requires_sponsorship": requires_sponsorship,
            "desired_salary": desired_salary,
            "gender": gender,
            "race_ethnicity": race_ethnicity,
            "veteran_status": veteran_status,
            "disability_status": disability_status,
        }
        result = _run_pipeline_sync(resume_path, cover_letter_path, job_url, tailoring_notes, overrides, log_q)
        _sessions[session_id]["result"] = result
        log_q.put({"type": "done", "result": result})

        while True:
            try:
                action = _action_q.get(timeout=3600)  # Wait up to 1h for user decision
            except queue.Empty:
                break
                
            if action["type"] == "submit":
                from agent.form_filler import submit_stored_form
                try:
                    ok = submit_stored_form()
                    _result_q.put({"ok": ok})
                except Exception as e:
                    _result_q.put({"ok": False, "msg": str(e)})
                break
                
            elif action["type"] == "edit_field":
                try:
                    res = _edit_field_impl(action["label"], action["new_value"])
                    _result_q.put(res)
                except Exception as e:
                    _result_q.put({"ok": False, "msg": str(e)})
                    
            elif action["type"] == "replace_resume":
                try:
                    res = _replace_resume_impl(action["new_path"])
                    _result_q.put(res)
                except Exception as e:
                    _result_q.put({"ok": False, "msg": str(e)})
                    
            elif action["type"] == "cancel":
                from agent.form_filler import _active_session
                sess = _active_session
                if sess.get("browser"):
                    try:
                        sess["browser"].close()
                        sess.get("playwright", type("x", (), {"stop": lambda self: None})()).stop()
                    except Exception:
                        pass
                    sess.clear()
                _result_q.put({"ok": True})
                break

    threading.Thread(target=worker, daemon=True).start()
    return {"session_id": session_id}


@app.get("/stream/{session_id}")
async def stream_logs(session_id: str):
    if session_id not in _sessions:
        return HTMLResponse("Session not found", status_code=404)

    log_q = _sessions[session_id]["queue"]

    async def event_gen():
        loop = asyncio.get_event_loop()
        while True:
            try:
                msg = await loop.run_in_executor(None, lambda: log_q.get(timeout=120))
                if isinstance(msg, dict):
                    yield f"data: {json.dumps(msg)}\n\n"
                    if msg.get("type") in ("done", "error"):
                        break
                else:
                    yield f"data: {json.dumps({'type': 'log', 'msg': msg})}\n\n"
            except Exception:
                yield f"data: {json.dumps({'type': 'error', 'msg': 'Timeout'})}\n\n"
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/submit")
async def submit():
    _action_q.put({"type": "submit"})
    res = await asyncio.get_event_loop().run_in_executor(None, _result_q.get)
    return res


def _edit_field_impl(label, new_value):
    from agent.form_filler import _active_session, _get_label
    sess = _active_session
    if not sess or not sess.get("page"):
        return {"ok": False, "msg": "No active session"}
    page = sess["page"]
    filled_fields = sess.get("filled_fields", [])
    
    inputs = page.locator("input[type='text'], input[type='email'], input[type='tel'], input:not([type])")
    for i in range(inputs.count()):
        field = inputs.nth(i)
        lbl = _get_label(page, field)
        if lbl and lbl.lower().strip() == label.lower().strip():
            field.fill(new_value)
            for idx, (f_label, _) in enumerate(filled_fields):
                if f_label.lower().strip() == label.lower().strip():
                    filled_fields[idx] = (label, new_value)
                    break
            else:
                filled_fields.append((label, new_value))
            return {"ok": True, "fields": filled_fields}
            
    for idx, (f_label, _) in enumerate(filled_fields):
        if f_label.lower().strip() == label.lower().strip():
            filled_fields[idx] = (label, f"{new_value} (manual - verify)")
            break
    else:
        filled_fields.append((label, f"{new_value} (manual - verify)"))
    return {"ok": True, "msg": "Field noted but could not be auto-updated in browser.", "fields": filled_fields}


@app.post("/edit_field")
async def edit_field(label: str = Form(...), new_value: str = Form(...)):
    _action_q.put({"type": "edit_field", "label": label, "new_value": new_value})
    res = await asyncio.get_event_loop().run_in_executor(None, _result_q.get)
    return res


def _replace_resume_impl(new_path):
    from agent.form_filler import _active_session
    sess = _active_session
    if not sess or not sess.get("page"):
        return {"ok": False, "msg": "No active session"}
    page = sess["page"]
    filled_fields = sess.get("filled_fields", [])
    
    file_input = page.locator("input[type='file']").first
    file_input.set_input_files(new_path)
    for idx, (f_label, _) in enumerate(filled_fields):
        if f_label == "Resume":
            filled_fields[idx] = ("Resume", new_path)
            break
    return {"ok": True, "fields": filled_fields}


@app.post("/replace_resume")
async def replace_resume(resume: UploadFile = File(...)):
    ext = Path(resume.filename).suffix
    new_path = os.path.join(UPLOAD_DIR, f"replaced_resume{ext}")
    with open(new_path, "wb") as f:
        shutil.copyfileobj(resume.file, f)
        
    _action_q.put({"type": "replace_resume", "new_path": new_path})
    res = await asyncio.get_event_loop().run_in_executor(None, _result_q.get)
    return res


@app.post("/cancel")
async def cancel():
    _action_q.put({"type": "cancel"})
    res = await asyncio.get_event_loop().run_in_executor(None, _result_q.get)
    return res


@app.get("/screenshot")
async def get_screenshot():
    path = os.path.join(OUTPUT_DIR, "form_screenshot.png")
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    return HTMLResponse("Not found", status_code=404)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _run_pipeline_sync(
    resume_path: str,
    cover_letter_path: str | None,
    job_url: str,
    tailoring_notes: str,
    overrides: dict,
    log_q: queue.Queue,
) -> dict:
    def log(msg: str):
        log_q.put(msg)

    try:
        from agent.jd_fetcher import fetch_jd
        from agent.form_filler import fill_and_submit_headless

        if os.path.exists(PROFILE_PATH):
            with open(PROFILE_PATH) as f:
                profile = yaml.safe_load(f)
        else:
            log("[*] New user detected. Extracting profile from resume...")
            from agent.docx_reader import read_docx
            extracted = read_docx(resume_path, use_claude=True)
            profile = {
                "resume_path": resume_path,
                "personal": extracted.get("personal", {}),
                "work_history": extracted.get("work_history", []),
                "education": extracted.get("education", []),
                "skills": extracted.get("skills", {}),
                "current": {}, "work_authorization": {}, "compensation": {}, "preferences": {}, "eeo": {}, "tailoring": {}
            }

        # Apply broad questions overrides
        profile.setdefault("current", {})["notice_period"] = overrides.get("notice_period") or profile.get("current", {}).get("notice_period", "")
        profile.setdefault("work_authorization", {})["canada"] = (overrides.get("authorized_canada") == "yes")
        profile.setdefault("work_authorization", {})["requires_sponsorship"] = (overrides.get("requires_sponsorship") == "yes")
        profile.setdefault("compensation", {})["desired_salary_cad"] = overrides.get("desired_salary") or profile.get("compensation", {}).get("desired_salary_cad", "")
        
        profile.setdefault("eeo", {})["gender"] = overrides.get("gender") or profile.get("eeo", {}).get("gender", "")
        profile.setdefault("eeo", {})["race_ethnicity"] = overrides.get("race_ethnicity") or profile.get("eeo", {}).get("race_ethnicity", "")
        profile.setdefault("eeo", {})["veteran_status"] = overrides.get("veteran_status") or profile.get("eeo", {}).get("veteran_status", "")
        profile.setdefault("eeo", {})["disability_status"] = overrides.get("disability_status") or profile.get("eeo", {}).get("disability_status", "")

        if tailoring_notes.strip():
            profile.setdefault("tailoring", {})["session_notes"] = tailoring_notes.strip()

        with open(PROFILE_PATH, "w") as f:
            yaml.dump(profile, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        log("[1/3] Fetching job description...")
        jd_data = fetch_jd(job_url)
        jd_text = jd_data["jd_text"]
        apply_url = jd_data["apply_url"]
        log(f"      {jd_data['job_title']} @ {jd_data['company']}")

        log("[2/3] Preparing resume for upload...")
        upload_path = _prepare_upload(resume_path, log)
        log(f"      Ready: {upload_path}")

        log("Step 3: Opening browser and filling form...")
        screenshot_path = os.path.join(OUTPUT_DIR, "form_screenshot.png")
        result = fill_and_submit_headless(
            apply_url=apply_url,
            resume_pdf_path=upload_path,
            cover_letter_path=cover_letter_path,
            profile=profile,
            jd_text=jd_text,
            screenshot_path=screenshot_path,
            log_fn=log,
        )

        result["job_title"] = jd_data.get("job_title", "")
        result["company"] = jd_data.get("company", "")
        result["screenshot_url"] = "/screenshot" if os.path.exists(screenshot_path) else None
        log("\nDone. Review below and confirm your decision.")
        return result

    except Exception as e:
        import traceback
        log(f"\nERROR: {e}\n{traceback.format_exc()}")
        return {"filled_ok": False, "error": str(e)}


def _prepare_upload(resume_path: str, log) -> str:
    # Most ATS accept .docx natively, so we just return the path directly 
    # instead of doing a messy conversion via Word AppleScript.
    return resume_path


# ── Launch ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser, time
    print("\nApply Agent running at http://localhost:8000")
    print("Opening browser...\n")
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8000")).start()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
